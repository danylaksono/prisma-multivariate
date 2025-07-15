import os
import sys
import json
import requests
import pandas as pd
import time
import logging
import re
from typing import Dict, List, Optional, Any
from requests.exceptions import RequestException, Timeout, HTTPError
from utils.api_utils import (
    APIError, ResponseError, RateLimitError,
    validate_api_response, validate_paper_entry,
    save_bibtex, save_results_to_csv, make_api_request
)
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
# OpenAlex doesn't require an API key, but we can use one for higher rate limits
API_KEY = os.getenv("OpenAlexAPIKey")  # Optional

class OpenAlexAPIError(APIError):
    """Custom exception for OpenAlex API related errors."""
    pass

def validate_openalex_response(response: requests.Response) -> Dict[str, Any]:
    """
    Validate and parse OpenAlex API response.
    
    Args:
        response: requests.Response object
        
    Returns:
        Dict containing parsed JSON data
        
    Raises:
        ResponseError: If response is invalid
        RateLimitError: If rate limit is exceeded
    """
    try:
        # Check HTTP status code
        response.raise_for_status()
        
        # Check content type
        content_type = response.headers.get('content-type', '')
        if 'application/json' not in content_type:
            raise ResponseError(f"Expected JSON response, got: {content_type}")
        
        # Parse JSON
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            raise ResponseError(f"Invalid JSON response: {e}")
        
        # Validate response structure
        if not isinstance(data, dict):
            raise ResponseError("Response is not a dictionary")
        
        # Check for error messages in OpenAlex response
        if 'error' in data:
            error_msg = data['error'].get('message', 'Unknown error')
            raise ResponseError(f"OpenAlex API Error: {error_msg}")
        
        # Check for results
        if 'results' not in data:
            raise ResponseError("Missing 'results' in response")
        
        return data
        
    except HTTPError as e:
        status_code = response.status_code
        if status_code == 429:
            raise RateLimitError("Rate limit exceeded")
        elif status_code == 401:
            raise ResponseError("Authentication failed - check API key")
        elif status_code == 403:
            raise ResponseError("Access forbidden - check API permissions")
        elif status_code == 400:
            raise ResponseError("Bad request - check query parameters")
        else:
            raise ResponseError(f"HTTP {status_code}: {e}")
    except Timeout:
        raise ResponseError("Request timed out")
    except RequestException as e:
        raise ResponseError(f"Network error: {e}")

def validate_openalex_paper_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and clean an OpenAlex paper entry from the API response.
    
    Args:
        entry: Raw paper entry from OpenAlex API
        
    Returns:
        Cleaned paper dictionary
    """
    if not isinstance(entry, dict):
        logging.warning(f"Invalid entry type: {type(entry)}")
        return {}
    
    # Extract and validate required fields
    paper = {}
    
    # DOI - can be None but should be string if present
    doi = entry.get("doi")
    if doi is not None and not isinstance(doi, str):
        logging.warning(f"Invalid DOI type: {type(doi)}")
        doi = None
    paper["doi"] = doi
    
    # Title - required field
    title = entry.get("title")
    if not title or not isinstance(title, str):
        logging.warning(f"Missing or invalid title: {title}")
        paper["title"] = "Unknown Title"
    else:
        paper["title"] = title.strip()
    
    # Authors - extract from authorships array
    authorships = entry.get("authorships", [])
    if authorships and isinstance(authorships, list):
        author_names = []
        for authorship in authorships:
            if isinstance(authorship, dict):
                author = authorship.get("author", {})
                if isinstance(author, dict):
                    display_name = author.get("display_name")
                    if display_name:
                        author_names.append(display_name)
        paper["authors"] = "; ".join(author_names) if author_names else None
    else:
        paper["authors"] = None
    
    # Year - validate and convert
    publication_date = entry.get("publication_date")
    if publication_date and isinstance(publication_date, str):
        try:
            # Extract year from date string (e.g., "2010" or "2010-01-01")
            year = int(publication_date.split('-')[0])
            if 1900 <= year <= 2030:  # Reasonable year range
                paper["year"] = year
            else:
                logging.warning(f"Year out of reasonable range: {year}")
                paper["year"] = None
        except (ValueError, IndexError):
            logging.warning(f"Invalid year format: {publication_date}")
            paper["year"] = None
    else:
        paper["year"] = None
    
    # Publication name - extract from primary_location
    primary_location = entry.get("primary_location", {})
    if isinstance(primary_location, dict):
        source = primary_location.get("source", {})
        if isinstance(source, dict):
            display_name = source.get("display_name")
            if display_name and isinstance(display_name, str):
                paper["publicationName"] = display_name.strip()
            else:
                paper["publicationName"] = None
        else:
            paper["publicationName"] = None
    else:
        paper["publicationName"] = None
    
    # URL - use OpenAlex URL
    openalex_id = entry.get("id")
    if openalex_id and isinstance(openalex_id, str):
        paper["url"] = openalex_id
    else:
        paper["url"] = None
    
    # Citation count
    cited_by_count = entry.get("cited_by_count")
    if cited_by_count is not None:
        try:
            paper["citedby-count"] = int(cited_by_count)
        except (ValueError, TypeError):
            logging.warning(f"Invalid citation count: {cited_by_count}")
            paper["citedby-count"] = 0
    else:
        paper["citedby-count"] = 0
    
    return paper

def get_openalex(query: str, venue_filter: Optional[str] = None, start_year: Optional[int] = None, 
                end_year: Optional[int] = None, output_dir: str = 'output', timeout: int = 30, 
                max_retries: int = 3, save_bibtex: bool = True) -> pd.DataFrame:
    """
    Fetch papers from OpenAlex API for given query and optional filters.
    Returns a DataFrame of unique papers and saves to CSV and optionally BibTeX.
    
    Parameters:
    - query: str, the search query
    - venue_filter: str, optional venue name or ISSN to filter by
    - start_year: int, starting publication year (inclusive)
    - end_year: int, ending publication year (inclusive)
    - output_dir: str, base directory for outputs
    - timeout: int, request timeout in seconds
    - max_retries: int, maximum number of retries for failed requests
    - save_bibtex: bool, whether to save BibTeX format output
    
    Raises ValueError on invalid inputs.
    """
    # Input validation
    if not isinstance(query, str) or len(query.strip()) == 0:
        raise ValueError("Query must be a non-empty string.")
    if venue_filter is not None and not isinstance(venue_filter, str):
        raise ValueError("venue_filter must be a string or None.")
    if start_year is not None and not isinstance(start_year, int):
        raise ValueError("start_year must be an integer or None.")
    if end_year is not None and not isinstance(end_year, int):
        raise ValueError("end_year must be an integer or None.")
    if start_year is not None and end_year is not None and start_year > end_year:
        raise ValueError("start_year must be <= end_year when both are provided.")
    if not (isinstance(timeout, int) and timeout > 0):
        raise ValueError("timeout must be a positive integer.")
    if not (isinstance(max_retries, int) and max_retries >= 0):
        raise ValueError("max_retries must be a non-negative integer.")
    if not isinstance(save_bibtex, bool):
        raise ValueError("save_bibtex must be a boolean.")
    
    # Clean inputs
    query = query.strip()
    venue_filter = venue_filter.strip() if venue_filter else None
    
    logging.info(f"Starting OpenAlex search for query: {query}")
    if venue_filter:
        logging.info(f"Venue filter: {venue_filter}")
    if start_year and end_year:
        logging.info(f"Year range: {start_year}-{end_year}")
    
    # Prepare output folder
    try:
        output_folder = os.path.join(os.getcwd(), output_dir, "openalex")
        os.makedirs(output_folder, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create output directory: {e}")
    
    all_papers = []  # Accumulate all papers in memory
    
    # Build query with filters
    full_query = query
    
    # Add venue filter if provided
    if venue_filter:
        # Check if it looks like an ISSN
        if re.match(r'^\d{4}-\d{3}[\dX]$', venue_filter):
            full_query += f" AND venue.issn:{venue_filter}"
        else:
            # Treat as venue name
            full_query += f' AND venue.display_name:"{venue_filter}"'
    
    # Add year filter if provided
    if start_year and end_year:
        full_query += f" AND from_publication_date:{start_year}-01-01 AND to_publication_date:{end_year}-12-31"
    elif start_year:
        full_query += f" AND from_publication_date:{start_year}-01-01"
    elif end_year:
        full_query += f" AND to_publication_date:{end_year}-12-31"
    
    logging.info(f"Processing query: {full_query}")
    
    page = 1
    batch = 1
    consecutive_errors = 0
    max_consecutive_errors = 3
    
    while True:
        logging.info(f"   Fetching batch {batch}, page: {page}")
        url = "https://api.openalex.org/works"
        headers = {
            "Accept": "application/json",
            "User-Agent": "SystematicReview/1.0"
        }
        
        # Add API key if available
        if API_KEY:
            headers["Authorization"] = f"Bearer {API_KEY}"
        
        params = {
            "search": full_query,
            "page": page,
            "per_page": 200,  # OpenAlex max per page
            "select": "id,doi,title,authorships,publication_date,primary_location,cited_by_count"
        }
        
        retries = max_retries
        while retries >= 0:
            try:
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
                
                # Validate OpenAlex response
                data = validate_openalex_response(response)
                
                # Check rate limit headers
                remaining = response.headers.get('X-RateLimit-Remaining')
                if remaining and int(remaining) < 10:
                    logging.warning(f"Low remaining requests: {remaining}. Sleeping 10s.")
                    time.sleep(10)
                
                results = data.get('results', [])
                if not results:
                    logging.info("No more results found.")
                    break
                
                # Validate and extract papers
                valid_papers = 0
                for result in results:
                    try:
                        paper = validate_openalex_paper_entry(result)
                        if paper:  # Only add if validation passed
                            all_papers.append(paper)
                            valid_papers += 1
                    except Exception as e:
                        logging.warning(f"Failed to process paper entry: {e}")
                        continue
                
                meta = data.get('meta', {})
                total_count = meta.get('count', 0)
                next_page = meta.get('next_cursor')
                
                logging.info(f"Fetched {len(results)} papers, {valid_papers} valid. Total so far: {len(all_papers)}. Total available: {total_count}")
                
                if not next_page:
                    break
                
                page += 1
                consecutive_errors = 0  # Reset error counter on success
                time.sleep(1)  # Short sleep between pages
                break  # Successful fetch, exit retry loop
            
            except RateLimitError as e:
                logging.error(f"Rate limit exceeded: {e}")
                time.sleep(60)  # Wait 1 minute before retry
                retries -= 1
                if retries < 0:
                    raise OpenAlexAPIError(f"Rate limit exceeded: {e}")
                    
            except ResponseError as e:
                logging.error(f"API response error: {e}")
                retries -= 1
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise OpenAlexAPIError(f"Too many consecutive errors ({consecutive_errors})")
                time.sleep(5 * (max_retries - retries))  # Exponential backoff
                if retries < 0:
                    raise OpenAlexAPIError(f"API response error: {e}")
                    
            except (RequestException, Timeout) as e:
                logging.error(f"Network error: {e}")
                retries -= 1
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise OpenAlexAPIError(f"Too many consecutive network errors ({consecutive_errors})")
                time.sleep(5 * (max_retries - retries))  # Exponential backoff
                if retries < 0:
                    raise
                    
            except Exception as e:
                logging.error(f"Unexpected error: {e}")
                retries -= 1
                if retries < 0:
                    raise
        
        batch += 1
    
    if not all_papers:
        logging.info("No papers found.")
        return pd.DataFrame()
    
    # Create DataFrame, de-dupe by DOI, save to CSV and BibTeX
    try:
        # Generate identifier for filename
        identifier = venue_filter if venue_filter else "general"
        if venue_filter and re.match(r'^\d{4}-\d{3}[\dX]$', venue_filter):
            # Use ISSN as identifier
            identifier = venue_filter
        else:
            # Use first few words of query as identifier
            words = query.split()[:3]
            identifier = "_".join(words).lower()
            identifier = re.sub(r'[^\w]', '', identifier)[:20]  # Clean and limit length
        
        # Save to CSV
        cleaned_csv_filename = f'cleanedPapers_openalex_{identifier}.csv'
        csv_path = os.path.join(output_folder, cleaned_csv_filename)
        final_df = save_results_to_csv(all_papers, csv_path)
        
        # Save to BibTeX if requested
        if save_bibtex:
            bibtex_filename = f'papers_openalex_{identifier}.bib'
            bibtex_path = os.path.join(output_folder, bibtex_filename)
            save_bibtex(all_papers, bibtex_path)
        
        logging.info(f"Total unique papers found: {len(final_df)}. Saved to {cleaned_csv_filename}")
        if save_bibtex:
            logging.info(f"BibTeX file saved to: {bibtex_filename}")
        
        return final_df
        
    except Exception as e:
        logging.error(f"Error processing results: {e}")
        raise OpenAlexAPIError(f"Failed to process results: {e}")

if __name__ == "__main__":
    # Example usage
    example_query = '(multivariate OR bivariate) AND (carto* OR visual* OR geovis*)'
    example_venue = '0317-7173'  # Cartographica ISSN
    example_start_year = 2010
    example_end_year = 2024
    
    try:
        # Search with venue filter
        get_openalex(example_query, venue_filter=example_venue, 
                    start_year=example_start_year, end_year=example_end_year, 
                    save_bibtex=True)
        
        # Search without venue filter
        get_openalex(example_query, start_year=example_start_year, 
                    end_year=example_end_year, save_bibtex=True)
        
    except OpenAlexAPIError as e:
        logging.error(f"OpenAlex API error: {e}")
        sys.exit(1)
    except ValueError as e:
        logging.error(f"Invalid input: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        sys.exit(1) 