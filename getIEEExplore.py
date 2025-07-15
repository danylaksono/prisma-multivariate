import os
import sys
import json
import requests
import pandas as pd
import time
import logging
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
API_KEY = os.getenv("IEEEXploreAPIKey")
if not API_KEY:
    logging.error("IEEE Xplore API key not found in environment variables.")
    raise ValueError("IEEE Xplore API key not found. Please set IEEEXploreAPIKey in .env file.")

class IEEEXploreAPIError(APIError):
    """Custom exception for IEEE Xplore API related errors."""
    pass

def validate_ieee_response(response: requests.Response) -> Dict[str, Any]:
    """
    Validate and parse IEEE Xplore API response.
    
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
        
        # Check for error messages in IEEE Xplore response
        if 'error' in data:
            error_msg = data['error'].get('message', 'Unknown error')
            raise ResponseError(f"IEEE Xplore API Error: {error_msg}")
        
        # Check for articles
        if 'articles' not in data:
            raise ResponseError("Missing 'articles' in response")
        
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

def validate_ieee_paper_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and clean an IEEE Xplore paper entry from the API response.
    
    Args:
        entry: Raw paper entry from IEEE Xplore API
        
    Returns:
        Cleaned paper dictionary
    """
    if not isinstance(entry, dict):
        logging.warning(f"Invalid entry type: {type(entry)}")
        return {}
    
    # Extract and validate required fields
    paper = {}
    
    # DOI
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
    
    # Authors - IEEE Xplore provides authors as a list
    authors = entry.get("authors", {}).get("authors", [])
    if authors and isinstance(authors, list):
        # Join author names
        author_names = []
        for author in authors:
            if isinstance(author, dict):
                full_name = author.get("full_name", "")
                if full_name:
                    author_names.append(full_name)
        paper["authors"] = "; ".join(author_names) if author_names else None
    else:
        paper["authors"] = None
    
    # Year - validate and convert
    year_str = entry.get("publication_year")
    if year_str and isinstance(year_str, str):
        try:
            year = int(year_str)
            if 1900 <= year <= 2030:  # Reasonable year range
                paper["year"] = year
            else:
                logging.warning(f"Year out of reasonable range: {year}")
                paper["year"] = None
        except (ValueError, TypeError):
            logging.warning(f"Invalid year format: {year_str}")
            paper["year"] = None
    else:
        paper["year"] = None
    
    # Publication name
    pub_name = entry.get("publication_title")
    if pub_name and isinstance(pub_name, str):
        paper["publicationName"] = pub_name.strip()
    else:
        paper["publicationName"] = None
    
    # URL
    url = entry.get("pdf_url") or entry.get("article_url")
    if url and isinstance(url, str):
        paper["url"] = url.strip()
    else:
        paper["url"] = None
    
    # Citation count
    cited_count = entry.get("citing_paper_count")
    if cited_count is not None:
        try:
            paper["citedby-count"] = int(cited_count)
        except (ValueError, TypeError):
            logging.warning(f"Invalid citation count: {cited_count}")
            paper["citedby-count"] = 0
    else:
        paper["citedby-count"] = 0
    
    # Additional IEEE-specific fields
    paper["ieee_article_number"] = entry.get("article_number")
    paper["ieee_pdf_url"] = entry.get("pdf_url")
    paper["ieee_abstract"] = entry.get("abstract")
    paper["ieee_keywords"] = entry.get("index_terms", {}).get("ieee_terms", {}).get("terms", [])
    
    return paper

def get_ieee_xplore(query: str, start_year: int, end_year: int, output_dir: str = 'output', 
                    timeout: int = 30, max_retries: int = 3, save_bibtex: bool = True) -> pd.DataFrame:
    """
    Fetch papers from IEEE Xplore API for given query and year range.
    Returns a DataFrame of unique papers and saves to CSV and optionally BibTeX.
    
    Parameters:
    - query: str, the search query
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
    if not (isinstance(start_year, int) and isinstance(end_year, int) and start_year <= end_year):
        raise ValueError("start_year and end_year must be integers with start_year <= end_year.")
    if not (isinstance(timeout, int) and timeout > 0):
        raise ValueError("timeout must be a positive integer.")
    if not (isinstance(max_retries, int) and max_retries >= 0):
        raise ValueError("max_retries must be a non-negative integer.")
    if not isinstance(save_bibtex, bool):
        raise ValueError("save_bibtex must be a boolean.")
    
    # Clean inputs
    query = query.strip()
    
    logging.info(f"Starting IEEE Xplore search for years: {start_year}-{end_year}, query: {query}")
    
    # Prepare output folder
    try:
        output_folder = os.path.join(os.getcwd(), output_dir, "ieee_xplore")
        os.makedirs(output_folder, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create output directory: {e}")
    
    all_papers = []  # Accumulate all papers in memory
    
    # Build query with year range
    full_query = f'("{query}") AND ({start_year} <= publication_year <= {end_year})'
    logging.info(f"Processing query: {full_query}")
    
    start_index = 1  # IEEE Xplore uses 1-based indexing
    batch = 1
    consecutive_errors = 0
    max_consecutive_errors = 3
    
    while True:
        logging.info(f"   Fetching batch {batch}, start_index: {start_index}")
        url = "https://ieeexplore.ieee.org/rest/search"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": API_KEY
        }
        params = {
            "querytext": full_query,
            "highlight": True,
            "returnFacets": ["ALL"],
            "returnType": "SEARCH",
            "pageNumber": start_index,
            "max_records": 200  # IEEE Xplore max per request
        }
        
        retries = max_retries
        while retries >= 0:
            try:
                response = make_api_request(url, headers, params, timeout, max_retries)
                
                # Validate IEEE Xplore specific response
                data = validate_ieee_response(response)
                
                # Check rate limit headers
                remaining = response.headers.get('X-RateLimit-Remaining')
                if remaining and int(remaining) < 10:
                    logging.warning(f"Low remaining requests: {remaining}. Sleeping 10s.")
                    time.sleep(10)
                
                articles = data.get('articles', [])
                if not articles:
                    logging.info("No more articles found.")
                    break
                
                # Validate and extract papers
                valid_papers = 0
                for article in articles:
                    try:
                        paper = validate_ieee_paper_entry(article)
                        if paper:  # Only add if validation passed
                            all_papers.append(paper)
                            valid_papers += 1
                    except Exception as e:
                        logging.warning(f"Failed to process article entry: {e}")
                        continue
                
                total_results = int(data.get('totalRecords', 0))
                start_index += 1  # IEEE Xplore uses page numbers
                logging.info(f"Fetched {len(articles)} articles, {valid_papers} valid. Total so far: {len(all_papers)}. Next page: {start_index}")
                
                if start_index > (total_results // 200) + 1:  # Check if we've reached the end
                    break
                
                consecutive_errors = 0  # Reset error counter on success
                time.sleep(1)  # Short sleep between pages
                break  # Successful fetch, exit retry loop
            
            except RateLimitError as e:
                logging.error(f"Rate limit exceeded: {e}")
                time.sleep(60)  # Wait 1 minute before retry
                retries -= 1
                if retries < 0:
                    raise IEEEXploreAPIError(f"Rate limit exceeded: {e}")
                    
            except ResponseError as e:
                logging.error(f"API response error: {e}")
                retries -= 1
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise IEEEXploreAPIError(f"Too many consecutive errors ({consecutive_errors})")
                time.sleep(5 * (max_retries - retries))  # Exponential backoff
                if retries < 0:
                    raise IEEEXploreAPIError(f"API response error: {e}")
                    
            except Exception as e:
                logging.error(f"Unexpected error: {e}")
                retries -= 1
                if retries < 0:
                    raise IEEEXploreAPIError(f"Unexpected error: {e}")
        
        batch += 1
    
    if not all_papers:
        logging.info("No papers found.")
        return pd.DataFrame()
    
    # Create DataFrame, de-dupe by DOI, save to CSV and BibTeX
    try:
        # Save to CSV
        csv_filename = f'ieee_papers_{start_year}_{end_year}.csv'
        csv_path = os.path.join(output_folder, csv_filename)
        final_df = save_results_to_csv(all_papers, csv_path)
        
        # Save to BibTeX if requested
        if save_bibtex:
            bibtex_filename = f'ieee_papers_{start_year}_{end_year}.bib'
            bibtex_path = os.path.join(output_folder, bibtex_filename)
            save_bibtex(all_papers, bibtex_path)
        
        logging.info(f"Total unique papers found: {len(final_df)}. Saved to {csv_filename}")
        if save_bibtex:
            logging.info(f"BibTeX file saved to: {bibtex_filename}")
        
        return final_df
        
    except Exception as e:
        logging.error(f"Error processing results: {e}")
        raise IEEEXploreAPIError(f"Failed to process results: {e}")

if __name__ == "__main__":
    # Example usage
    example_query = '(multivariate OR bivariate) AND (carto* OR visual* OR geovis*)'
    example_start_year = 2010
    example_end_year = 2024
    
    try:
        # Save both CSV and BibTeX formats
        get_ieee_xplore(example_query, example_start_year, example_end_year, save_bibtex=True)
    except IEEEXploreAPIError as e:
        logging.error(f"IEEE Xplore API error: {e}")
        sys.exit(1)
    except ValueError as e:
        logging.error(f"Invalid input: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        sys.exit(1) 