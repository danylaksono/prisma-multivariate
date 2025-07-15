#!/usr/bin/env python3
"""
DBLP API script with venue-specific search capabilities.
Allows targeting specific journals/conferences like EuroVis, Eurographics, etc.
"""

import os
import sys
import json
import requests
import pandas as pd
import time
import logging
from typing import Dict, List, Optional, Any
from requests.exceptions import RequestException, Timeout, HTTPError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from utils.api_utils import (
    APIError, ResponseError, RateLimitError,
    validate_api_response, validate_paper_entry,
    save_bibtex, save_results_to_csv, make_api_request
)
from dotenv import load_dotenv
import ssl

# Load environment variables
load_dotenv()

class DBLPAPIError(APIError):
    """Custom exception for DBLP API related errors."""
    pass

def create_session():
    """Create a requests session with SSL retry configuration."""
    session = requests.Session()
    
    # Configure retry strategy for SSL issues with more aggressive settings
    retry_strategy = Retry(
        total=10,  # Increased total retries
        backoff_factor=1,  # Reduced backoff factor for faster retries
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        # Add specific SSL error handling
        raise_on_redirect=False,
        raise_on_status=False
    )
    
    class TLSAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            kwargs['ssl_context'] = context
            return super().init_poolmanager(*args, **kwargs)
    adapter = TLSAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Add SSL configuration for better compatibility
    session.verify = True  # Enable SSL verification
    session.headers.update({
        'User-Agent': 'SystematicReview/1.0 (DBLP API Client)',
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive'
    })
    
    return session

def validate_dblp_response(response: requests.Response) -> Dict[str, Any]:
    """
    Validate and parse DBLP API response.
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
        
        # Check for error messages in DBLP response
        if 'error' in data:
            error_msg = data['error'].get('message', 'Unknown error')
            raise ResponseError(f"DBLP API Error: {error_msg}")
        
        # Check for hits - DBLP API returns hits under result.hits
        if 'result' not in data:
            raise ResponseError("Missing 'result' in response")
        
        result = data['result']
        if 'hits' not in result:
            raise ResponseError("Missing 'hits' in result")
        
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

def validate_dblp_paper_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and clean a DBLP paper entry from the API response.
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
    
    # Authors - DBLP provides authors as a list
    authors = entry.get("authors", {}).get("author", [])
    if authors and isinstance(authors, list):
        # Join author names
        author_names = []
        for author in authors:
            if isinstance(author, dict):
                full_name = author.get("text", "")
                if full_name:
                    author_names.append(full_name)
        paper["authors"] = "; ".join(author_names) if author_names else None
    else:
        paper["authors"] = None
    
    # Year - validate and convert
    year_str = entry.get("year")
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
    pub_name = entry.get("venue")
    if pub_name and isinstance(pub_name, str):
        paper["publicationName"] = pub_name.strip()
    else:
        paper["publicationName"] = None
    
    # URL
    url = entry.get("url")
    if url and isinstance(url, str):
        paper["url"] = url.strip()
    else:
        paper["url"] = None
    
    # Citation count (DBLP doesn't provide citation counts)
    paper["citedby-count"] = 0
    
    # Additional DBLP-specific fields
    paper["dblp_key"] = entry.get("key")
    paper["dblp_type"] = entry.get("type")
    paper["dblp_ee"] = entry.get("ee")  # Electronic edition URL
    paper["dblp_pages"] = entry.get("pages")
    paper["dblp_volume"] = entry.get("volume")
    paper["dblp_number"] = entry.get("number")
    paper["dblp_publisher"] = entry.get("publisher")
    paper["dblp_series"] = entry.get("series")
    
    return paper

def get_dblp_venue(query: str, venue: str, start_year: int, end_year: int, 
                   output_dir: str = 'output', timeout: int = 30, max_retries: int = 3, 
                   save_bibtex_format: bool = True) -> pd.DataFrame:
    """
    Fetch papers from DBLP API for a specific venue with year-by-year search.
    
    Parameters:
    - query: str, the search query (e.g., "multivariate")
    - venue: str, the target venue (e.g., "EuroVis", "Eurographics")
    - start_year: int, starting publication year (inclusive)
    - end_year: int, ending publication year (inclusive)
    - output_dir: str, base directory for outputs
    - timeout: int, request timeout in seconds
    - max_retries: int, maximum number of retries for failed requests
    - save_bibtex_format: bool, whether to save BibTeX format output
    """
    # Input validation
    if not isinstance(query, str) or len(query.strip()) == 0:
        raise ValueError("Query must be a non-empty string.")
    if not isinstance(venue, str) or len(venue.strip()) == 0:
        raise ValueError("Venue must be a non-empty string.")
    if not (isinstance(start_year, int) and isinstance(end_year, int) and start_year <= end_year):
        raise ValueError("start_year and end_year must be integers with start_year <= end_year.")
    
    # Clean inputs
    query = query.strip()
    venue = venue.strip()
    
    logging.info(f"Starting DBLP search for venue '{venue}' for years: {start_year}-{end_year}, query: {query}")
    
    # Prepare output folder
    try:
        output_folder = os.path.join(os.getcwd(), output_dir, "dblp")
        os.makedirs(output_folder, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create output directory: {e}")
    
    all_papers = []  # Accumulate all papers in memory
    
    # Create session with SSL retry configuration
    session = create_session()
    
    # Search year by year to avoid complex query syntax issues
    for year in range(start_year, end_year + 1):
        logging.info(f"Searching year {year} for venue '{venue}'...")
        
        # Build query for this year with venue filter
        year_query = f"{query} {venue} year:{year}"
        
        start_index = 0  # DBLP uses 0-based indexing
        batch = 1
        consecutive_errors = 0
        max_consecutive_errors = 3
        done = False
        while True:
            logging.info(f"   Fetching batch {batch}, start_index: {start_index} for year {year}")
            url = "https://dblp.org/search/publ/api"
            # Use session headers instead of creating new ones
            params = {
                "q": year_query,
                "h": 50,  # Further reduced batch size to avoid SSL issues
                "f": start_index,  # First hit
                "format": "json"
            }
            
            retries = max_retries
            while retries >= 0:
                try:
                    response = session.get(url, params=params, timeout=timeout)
                    
                    # Validate DBLP specific response
                    data = validate_dblp_response(response)
                    
                    # Check rate limit headers
                    remaining = response.headers.get('X-RateLimit-Remaining')
                    if remaining and int(remaining) < 10:
                        logging.warning(f"Low remaining requests: {remaining}. Sleeping 10s.")
                        time.sleep(10)
                    
                    hits = data.get('result', {}).get('hits', {}).get('hit', [])
                    total_results = int(data.get('result', {}).get('hits', {}).get('@total', 0))
                    batch_size = params["h"] if "h" in params else 50

                    valid_papers = 0
                    for hit in hits:
                        try:
                            info = hit.get('info', {})
                            paper = validate_dblp_paper_entry(info)
                            
                            # Additional venue filtering to ensure we get the right venue
                            paper_venue = paper.get('publicationName', '')
                            if paper and venue.lower() in paper_venue.lower():
                                all_papers.append(paper)
                                valid_papers += 1
                            elif paper:
                                logging.debug(f"Skipping paper from venue: {paper_venue}")
                                
                        except Exception as e:
                            logging.warning(f"Failed to process hit entry: {e}")
                            continue

                    start_index += len(hits)
                    logging.info(f"Fetched {len(hits)} hits, {valid_papers} valid for year {year}. Total so far: {len(all_papers)}. Next start: {start_index}/{total_results}")

                    # Break if fewer hits than batch size (last page)
                    if len(hits) < batch_size:
                        logging.info(f"Fetched final batch for year {year} (less than batch size).")
                        done = True
                        consecutive_errors = 0
                        break
                    else:
                        done = False
                        consecutive_errors = 0
                        time.sleep(3)
                        break
                
                except RateLimitError as e:
                    logging.error(f"Rate limit exceeded: {e}")
                    time.sleep(60)  # Wait 1 minute before retry
                    retries -= 1
                    if retries < 0:
                        raise DBLPAPIError(f"Rate limit exceeded: {e}")
                        
                except ResponseError as e:
                    logging.error(f"API response error: {e}")
                    retries -= 1
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        raise DBLPAPIError(f"Too many consecutive errors ({consecutive_errors})")
                    time.sleep(5 * (max_retries - retries))  # Exponential backoff
                    if retries < 0:
                        raise DBLPAPIError(f"API response error: {e}")
                        
                except Exception as e:
                    logging.error(f"Unexpected error: {e}")
                    retries -= 1
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        raise DBLPAPIError(f"Too many consecutive errors ({consecutive_errors})")
                    time.sleep(5 * (max_retries - retries))  # Exponential backoff
                    if retries < 0:
                        raise DBLPAPIError(f"Unexpected error: {e}")
            
            batch += 1
            if done:
                break
    
    if not all_papers:
        logging.info("No papers found.")
        return pd.DataFrame()
    
    # Create DataFrame, de-dupe by DOI, save to CSV and BibTeX
    try:
        # Save to CSV
        csv_filename = f'dblp_{venue.replace(" ", "_")}_{start_year}_{end_year}.csv'
        csv_path = os.path.join(output_folder, csv_filename)
        final_df = save_results_to_csv(all_papers, csv_path)
        
        # Save to BibTeX if requested
        if save_bibtex_format:
            bibtex_filename = f'dblp_{venue.replace(" ", "_")}_{start_year}_{end_year}.bib'
            bibtex_path = os.path.join(output_folder, bibtex_filename)
            save_bibtex(all_papers, bibtex_path)
        
        logging.info(f"Total unique papers found: {len(final_df)}. Saved to {csv_filename}")
        if save_bibtex_format:
            logging.info(f"BibTeX file saved to: {bibtex_filename}")
        
        return final_df
        
    except Exception as e:
        logging.error(f"Error processing results: {e}")
        raise DBLPAPIError(f"Failed to process results: {e}")

if __name__ == "__main__":
    # Example usage for EuroVis
    example_query = 'multivariate'
    example_venue = 'EuroVis'
    example_start_year = 2020
    example_end_year = 2025  
    
    try:
        # Save both CSV and BibTeX formats
        get_dblp_venue(example_query, example_venue, example_start_year, example_end_year, save_bibtex_format=True)
    except DBLPAPIError as e:
        logging.error(f"DBLP API error: {e}")
        sys.exit(1)
    except ValueError as e:
        logging.error(f"Invalid input: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        sys.exit(1) 