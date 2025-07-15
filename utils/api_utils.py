import os
import requests
import json
import pandas as pd
import time
import logging
import re
from typing import Dict, List, Optional, Any
from requests.exceptions import RequestException, Timeout, HTTPError
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

class APIError(Exception):
    """Base exception for API related errors."""
    pass

class ResponseError(APIError):
    """Exception for invalid API responses."""
    pass

class RateLimitError(APIError):
    """Exception for rate limit exceeded."""
    pass

def validate_api_response(response: requests.Response, expected_content_type: str = 'application/json') -> Dict[str, Any]:
    """
    Validate and parse API response.
    
    Args:
        response: requests.Response object
        expected_content_type: Expected content type (default: application/json)
        
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
        if expected_content_type not in content_type:
            raise ResponseError(f"Expected {expected_content_type} response, got: {content_type}")
        
        # Parse JSON
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            raise ResponseError(f"Invalid JSON response: {e}")
        
        # Validate response structure
        if not isinstance(data, dict):
            raise ResponseError("Response is not a dictionary")
        
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

def validate_paper_entry(entry: Dict[str, Any], required_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Validate and clean a paper entry from the API response.
    
    Args:
        entry: Raw paper entry from API
        required_fields: List of required field names
        
    Returns:
        Cleaned paper dictionary
    """
    if not isinstance(entry, dict):
        logging.warning(f"Invalid entry type: {type(entry)}")
        return {}
    
    # Extract and validate required fields
    paper = {}
    
    # DOI - can be None but should be string if present
    doi = entry.get("doi") or entry.get("prism:doi")
    if doi is not None and not isinstance(doi, str):
        logging.warning(f"Invalid DOI type: {type(doi)}")
        doi = None
    paper["doi"] = doi
    
    # Title - required field
    title = entry.get("title") or entry.get("dc:title")
    if not title or not isinstance(title, str):
        logging.warning(f"Missing or invalid title: {title}")
        paper["title"] = "Unknown Title"
    else:
        paper["title"] = title.strip()
    
    # Authors - can be None but should be string if present
    authors = entry.get("authors") or entry.get("dc:creator") or entry.get("author")
    if authors is not None and not isinstance(authors, str):
        logging.warning(f"Invalid authors type: {type(authors)}")
        authors = None
    paper["authors"] = authors
    
    # Year - validate and convert
    year_str = entry.get("year") or entry.get("prism:coverDisplayDate") or entry.get("publicationYear")
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
    pub_name = entry.get("publicationName") or entry.get("prism:publicationName") or entry.get("journal")
    if pub_name and isinstance(pub_name, str):
        paper["publicationName"] = pub_name.strip()
    else:
        paper["publicationName"] = None
    
    # URL
    url = entry.get("url") or entry.get("prism:url")
    if url and isinstance(url, str):
        paper["url"] = url.strip()
    else:
        paper["url"] = None
    
    # Citation count
    cited_count = entry.get("citedby-count") or entry.get("citationCount") or entry.get("citations")
    if cited_count is not None:
        try:
            paper["citedby-count"] = int(cited_count)
        except (ValueError, TypeError):
            logging.warning(f"Invalid citation count: {cited_count}")
            paper["citedby-count"] = 0
    else:
        paper["citedby-count"] = 0
    
    # Check required fields
    if required_fields:
        for field in required_fields:
            if field not in paper or paper[field] is None:
                logging.warning(f"Missing required field: {field}")
                return {}
    
    return paper

def clean_bibtex_key(title: str, authors: str, year: Optional[int], doi: Optional[str] = None, existing_keys: Optional[set] = None) -> str:
    """
    Generate a clean BibTeX key from title, authors, and year.
    Format: lastname + year + firstword (with suffix if duplicate)
    
    Args:
        title: Paper title
        authors: Author string
        year: Publication year
        doi: DOI for uniqueness (optional)
        existing_keys: Set of existing keys to check for duplicates
        
    Returns:
        Clean BibTeX key
    """
    # Extract first author's last name
    if authors:
        # Split by common separators and get first author
        author_parts = re.split(r'[,;]', authors.strip())
        first_author = author_parts[0].strip()
        # Extract last name (everything after last space)
        last_name = first_author.split()[-1] if first_author else "unknown"
    else:
        last_name = "unknown"
    
    # Get first word from title
    if title:
        # Remove special characters and get first word
        clean_title = re.sub(r'[^\w\s]', '', title.lower())
        words = clean_title.split()
        if words:
            first_word = words[0]
        else:
            first_word = "unknown"
    else:
        first_word = "unknown"
    
    # Create base key: lastname + year + firstword
    year_part = str(year) if year else "unknown"
    base_key = f"{last_name}{year_part}{first_word}"
    
    # Clean the base key (remove special characters, limit length)
    base_key = re.sub(r'[^\w]', '', base_key)[:50]  # Limit to 50 chars
    
    # Check for duplicates and add suffix if needed
    if existing_keys is None:
        existing_keys = set()
    
    key = base_key
    suffix = 1
    
    while key in existing_keys:
        # Add suffix to make it unique
        suffix_key = f"{base_key}{suffix}"
        if len(suffix_key) <= 50:  # Still within length limit
            key = suffix_key
        else:
            # If too long, truncate base key and add suffix
            max_base_length = 50 - len(str(suffix))
            key = f"{base_key[:max_base_length]}{suffix}"
        suffix += 1
    
    return key

def paper_to_bibtex(paper: Dict[str, Any], existing_keys: Optional[set] = None) -> str:
    """
    Convert a paper dictionary to BibTeX format.
    
    Args:
        paper: Paper dictionary with API data
        existing_keys: Set of existing keys to avoid duplicates
        
    Returns:
        BibTeX entry as string
    """
    # Generate BibTeX key
    key = clean_bibtex_key(
        paper.get('title', ''), 
        paper.get('authors', ''), 
        paper.get('year'),
        paper.get('doi'),
        existing_keys
    )
    
    # Start BibTeX entry
    bibtex = f"@article{{{key},\n"
    
    # Add fields
    if paper.get('title'):
        # Escape special characters in title
        title = paper['title'].replace('{', '\\{').replace('}', '\\}')
        bibtex += f"  title = {{{title}}},\n"
    
    if paper.get('authors'):
        # Clean and format authors
        authors = paper['authors'].replace('{', '\\{').replace('}', '\\}')
        bibtex += f"  author = {{{authors}}},\n"
    
    if paper.get('year'):
        bibtex += f"  year = {{{paper['year']}}},\n"
    
    if paper.get('publicationName'):
        journal = paper['publicationName'].replace('{', '\\{').replace('}', '\\}')
        bibtex += f"  journal = {{{journal}}},\n"
    
    if paper.get('doi'):
        bibtex += f"  doi = {{{paper['doi']}}},\n"
    
    if paper.get('url'):
        bibtex += f"  url = {{{paper['url']}}},\n"
    
    if paper.get('citedby-count', 0) > 0:
        bibtex += f"  note = {{Citations: {paper['citedby-count']}}},\n"
    
    # Remove trailing comma and close
    bibtex = bibtex.rstrip(',\n') + '\n}\n\n'
    
    return bibtex

def save_bibtex(papers: List[Dict[str, Any]], output_path: str) -> None:
    """
    Save papers to BibTeX file.
    
    Args:
        papers: List of paper dictionaries
        output_path: Path to save BibTeX file
    """
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header comment
            f.write(f"% BibTeX entries generated from API\n")
            f.write(f"% Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"% Total papers: {len(papers)}\n\n")
            
            # Track existing keys to avoid duplicates
            existing_keys = set()
            
            # Write each paper
            for paper in papers:
                bibtex_entry = paper_to_bibtex(paper, existing_keys)
                f.write(bibtex_entry)
                
                # Extract the key from the entry and add to existing keys
                # The key is between @article{ and the first comma
                key_start = bibtex_entry.find('@article{') + 9
                key_end = bibtex_entry.find(',', key_start)
                if key_end != -1:
                    key = bibtex_entry[key_start:key_end]
                    existing_keys.add(key)
        
        logging.info(f"BibTeX file saved to: {output_path}")
        
    except Exception as e:
        logging.error(f"Error saving BibTeX file: {e}")
        raise APIError(f"Failed to save BibTeX file: {e}")

def save_results_to_csv(papers: List[Dict[str, Any]], output_path: str) -> pd.DataFrame:
    """
    Save papers to CSV file and return DataFrame.
    
    Args:
        papers: List of paper dictionaries
        output_path: Path to save CSV file
        
    Returns:
        DataFrame of papers
    """
    try:
        df = pd.DataFrame(papers)
        
        # Remove rows with missing DOIs for deduplication
        df_with_doi = df[df['doi'].notna()]
        df_without_doi = df[df['doi'].isna()]
        
        # Deduplicate by DOI
        final_df = df_with_doi.drop_duplicates(subset='doi')
        
        # Add back papers without DOI (they can't be deduplicated)
        final_df = pd.concat([final_df, df_without_doi], ignore_index=True)
        
        # Save to CSV
        final_df.to_csv(output_path, index=False)
        
        logging.info(f"CSV file saved to: {output_path}")
        logging.info(f"Total unique papers: {len(final_df)}")
        logging.info(f"Papers with DOI: {len(df_with_doi)}, Papers without DOI: {len(df_without_doi)}")
        
        return final_df
        
    except Exception as e:
        logging.error(f"Error saving CSV file: {e}")
        raise APIError(f"Failed to save CSV file: {e}")

def make_api_request(url: str, headers: Dict[str, str], params: Dict[str, Any], 
                    timeout: int = 30, max_retries: int = 3) -> requests.Response:
    """
    Make an API request with retry logic.
    
    Args:
        url: API endpoint URL
        headers: Request headers
        params: Query parameters
        timeout: Request timeout in seconds
        max_retries: Maximum number of retries
        
    Returns:
        requests.Response object
        
    Raises:
        RateLimitError: If rate limit is exceeded
        ResponseError: If API returns an error
    """
    retries = max_retries
    consecutive_errors = 0
    max_consecutive_errors = 3
    
    while retries >= 0:
        try:
            # Add SSL verification settings for better compatibility
            session = requests.Session()
            session.verify = True  # Enable SSL verification
            
            # Add retry strategy for SSL issues
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            
            response = session.get(url, headers=headers, params=params, timeout=timeout)
            
            # Validate response
            validate_api_response(response)
            
            # Check rate limit headers
            remaining = response.headers.get('X-RateLimit-Remaining')
            if remaining and int(remaining) < 10:
                logging.warning(f"Low remaining requests: {remaining}. Sleeping 10s.")
                time.sleep(10)
            
            return response
            
        except RateLimitError as e:
            logging.error(f"Rate limit exceeded: {e}")
            time.sleep(60)  # Wait 1 minute before retry
            retries -= 1
            if retries < 0:
                raise
                
        except ResponseError as e:
            logging.error(f"API response error: {e}")
            retries -= 1
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                raise APIError(f"Too many consecutive errors ({consecutive_errors})")
            time.sleep(5 * (max_retries - retries))  # Exponential backoff
            if retries < 0:
                raise
                
        except (RequestException, Timeout) as e:
            logging.error(f"Network error: {e}")
            retries -= 1
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                raise APIError(f"Too many consecutive network errors ({consecutive_errors})")
            time.sleep(5 * (max_retries - retries))  # Exponential backoff
            if retries < 0:
                raise
                
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            retries -= 1
            if retries < 0:
                raise 