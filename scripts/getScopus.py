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
API_KEY = os.getenv("APIKey")
if not API_KEY:
    logging.error("API key not found in environment variables.")
    raise ValueError("API key not found. Please set it in .env file.")

class ScopusAPIError(APIError):
    """Custom exception for Scopus API related errors."""
    pass

def validate_scopus_response(response: requests.Response) -> Dict[str, Any]:
    """
    Validate and parse Scopus API response.
    
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
        
        # Check for error messages in Scopus response
        if 'service-error' in data:
            error_msg = data['service-error'].get('error', {}).get('error-message', 'Unknown error')
            raise ResponseError(f"Scopus API Error: {error_msg}")
        
        # Check for search-results
        if 'search-results' not in data:
            raise ResponseError("Missing 'search-results' in response")
        
        search_results = data['search-results']
        if not isinstance(search_results, dict):
            raise ResponseError("'search-results' is not a dictionary")
        
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

def validate_scopus_paper_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and clean a Scopus paper entry from the API response.
    
    Args:
        entry: Raw paper entry from Scopus API
        
    Returns:
        Cleaned paper dictionary
    """
    if not isinstance(entry, dict):
        logging.warning(f"Invalid entry type: {type(entry)}")
        return {}
    
    # Extract and validate required fields
    paper = {}
    
    # DOI - can be None but should be string if present
    doi = entry.get("prism:doi")
    if doi is not None and not isinstance(doi, str):
        logging.warning(f"Invalid DOI type: {type(doi)}")
        doi = None
    paper["doi"] = doi
    
    # Title - required field
    title = entry.get("dc:title")
    if not title or not isinstance(title, str):
        logging.warning(f"Missing or invalid title: {title}")
        paper["title"] = "Unknown Title"
    else:
        paper["title"] = title.strip()
    
    # Authors - can be None but should be string if present
    authors = entry.get("dc:creator")
    if authors is not None and not isinstance(authors, str):
        logging.warning(f"Invalid authors type: {type(authors)}")
        authors = None
    paper["authors"] = authors
    
    # Year - validate and convert
    year_str = entry.get("prism:coverDisplayDate")
    if year_str and isinstance(year_str, str):
        try:
            # Extract year from date string (e.g., "2010" or "2010-01-01")
            year = int(year_str.split('-')[0])
            if 1900 <= year <= 2030:  # Reasonable year range
                paper["year"] = year
            else:
                logging.warning(f"Year out of reasonable range: {year}")
                paper["year"] = None
        except (ValueError, IndexError):
            logging.warning(f"Invalid year format: {year_str}")
            paper["year"] = None
    else:
        paper["year"] = None
    
    # Publication name
    pub_name = entry.get("prism:publicationName")
    if pub_name and isinstance(pub_name, str):
        paper["publicationName"] = pub_name.strip()
    else:
        paper["publicationName"] = None
    
    # URL
    url = entry.get("prism:url")
    if url and isinstance(url, str):
        paper["url"] = url.strip()
    else:
        paper["url"] = None
    
    # Citation count
    cited_count = entry.get("citedby-count")
    if cited_count is not None:
        try:
            paper["citedby-count"] = int(cited_count)
        except (ValueError, TypeError):
            logging.warning(f"Invalid citation count: {cited_count}")
            paper["citedby-count"] = 0
    else:
        paper["citedby-count"] = 0
    
    return paper



def get_scopus(issn, query, start_year, end_year, output_dir='output', timeout=30, max_retries=3, save_bibtex=True):
    """
    Fetch papers from Scopus API for given ISSN, query, and year range.
    Returns a DataFrame of unique papers and saves to CSV and optionally BibTeX.
    
    Parameters:
    - issn: str, journal ISSN
    - query: str, the search query for TITLE-ABS-KEY
    - start_year: int, starting publication year (inclusive, uses AFT for after)
    - end_year: int, ending publication year (exclusive, uses BEF for before)
    - output_dir: str, base directory for outputs
    - timeout: int, request timeout in seconds
    - max_retries: int, maximum number of retries for failed requests
    - save_bibtex: bool, whether to save BibTeX format output
    
    Raises ValueError on invalid inputs.
    """
    # Input validation
    if not isinstance(issn, str) or len(issn.strip()) == 0:
        raise ValueError("ISSN must be a non-empty string.")
    if not isinstance(query, str) or len(query.strip()) == 0:
        raise ValueError("Query must be a non-empty string.")
    if not (isinstance(start_year, int) and isinstance(end_year, int) and start_year < end_year):
        raise ValueError("start_year and end_year must be integers with start_year < end_year.")
    if not (isinstance(timeout, int) and timeout > 0):
        raise ValueError("timeout must be a positive integer.")
    if not (isinstance(max_retries, int) and max_retries >= 0):
        raise ValueError("max_retries must be a non-negative integer.")
    if not isinstance(save_bibtex, bool):
        raise ValueError("save_bibtex must be a boolean.")
    
    # Clean inputs
    issn = issn.strip()
    query = query.strip()
    
    logging.info(f"Starting Scopus search for ISSN: {issn}, years: {start_year}-{end_year}, query: {query}")
    
    # Prepare output folder
    try:
        output_folder = os.path.join(os.getcwd(), output_dir, issn)
        os.makedirs(output_folder, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create output directory: {e}")
    
    all_papers = []  # Accumulate all papers in memory
    
    full_query = f"TITLE-ABS-KEY({query}) AND ISSN({issn}) AND (PUBYEAR AFT {start_year - 1} AND PUBYEAR BEF {end_year})"
    logging.info(f"Processing query: {full_query}")
    
    start_index = 0
    batch = 1
    consecutive_errors = 0
    max_consecutive_errors = 3
    
    while True:
        logging.info(f"   Fetching batch {batch}, start_index: {start_index}")
        url = "https://api.elsevier.com/content/search/scopus"
        headers = {"Accept": "application/json", "X-ELS-APIKey": API_KEY}
        params = {
            "query": full_query,
            "sort": "date",
            "start": start_index,
            "count": 200  # Larger page size for efficiency
        }
        
        retries = max_retries
        while retries >= 0:
            try:
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
                
                # Validate Scopus response
                data = validate_scopus_response(response)
                
                # Check rate limit headers
                remaining = response.headers.get('X-RateLimit-Remaining')
                if remaining and int(remaining) < 10:
                    logging.warning(f"Low remaining requests: {remaining}. Sleeping 10s.")
                    time.sleep(10)
                
                entries = data.get('search-results', {}).get('entry', [])
                if not entries:
                    logging.info("No more entries found.")
                    break
                
                # Validate and extract papers
                valid_papers = 0
                for entry in entries:
                    try:
                        paper = validate_scopus_paper_entry(entry)
                        if paper:  # Only add if validation passed
                            all_papers.append(paper)
                            valid_papers += 1
                    except Exception as e:
                        logging.warning(f"Failed to process paper entry: {e}")
                        continue
                
                total_results = int(data['search-results'].get('opensearch:totalResults', 0))
                start_index += params['count']
                logging.info(f"Fetched {len(entries)} papers, {valid_papers} valid. Total so far: {len(all_papers)}. Next start: {start_index}/{total_results}")
                
                if start_index >= total_results:
                    break
                
                consecutive_errors = 0  # Reset error counter on success
                time.sleep(1)  # Short sleep between pages
                break  # Successful fetch, exit retry loop
            
            except RateLimitError as e:
                logging.error(f"Rate limit exceeded: {e}")
                time.sleep(60)  # Wait 1 minute before retry
                retries -= 1
                if retries < 0:
                    raise ScopusAPIError(f"Rate limit exceeded: {e}")
                    
            except ResponseError as e:
                logging.error(f"API response error: {e}")
                retries -= 1
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise ScopusAPIError(f"Too many consecutive errors ({consecutive_errors})")
                time.sleep(5 * (max_retries - retries))  # Exponential backoff
                if retries < 0:
                    raise ScopusAPIError(f"API response error: {e}")
                    
            except (RequestException, Timeout) as e:
                logging.error(f"Network error: {e}")
                retries -= 1
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise ScopusAPIError(f"Too many consecutive network errors ({consecutive_errors})")
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
        # Save to CSV
        cleaned_csv_filename = f'cleanedPapers_{issn}.csv'
        csv_path = os.path.join(output_folder, cleaned_csv_filename)
        final_df = save_results_to_csv(all_papers, csv_path)
        
        # Save to BibTeX if requested
        if save_bibtex:
            bibtex_filename = f'papers_{issn}.bib'
            bibtex_path = os.path.join(output_folder, bibtex_filename)
            save_bibtex(all_papers, bibtex_path)
        
        logging.info(f"Total unique papers found: {len(final_df)}. Saved to {cleaned_csv_filename}")
        if save_bibtex:
            logging.info(f"BibTeX file saved to: {bibtex_filename}")
        
        return final_df
        
    except Exception as e:
        logging.error(f"Error processing results: {e}")
        raise ScopusAPIError(f"Failed to process results: {e}")

if __name__ == "__main__":
    # Example usage
    example_issn = '0317-7173'  # Cartographica
    example_query = '(multivariate OR bivariate) AND (carto* OR visual* OR geovis*)'
    example_start_year = 2010
    example_end_year = 2024
    
    try:
        # Save both CSV and BibTeX formats
        get_scopus(example_issn, example_query, example_start_year, example_end_year, save_bibtex=True)
    except ScopusAPIError as e:
        logging.error(f"Scopus API error: {e}")
        sys.exit(1)
    except ValueError as e:
        logging.error(f"Invalid input: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        sys.exit(1) 