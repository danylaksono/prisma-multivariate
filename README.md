# Systematic Review Paper Collection

This project provides tools to collect academic papers from different APIs for systematic reviews. Currently supports Scopus, IEEE Xplore, DBLP, and Web of Science APIs.

## Project Structure

```
systematic_review/
├── utils/
│   ├── __init__.py
│   └── api_utils.py          # Shared utilities for API operations
├── getScopus.py              # Scopus API integration
├── getIEEExplore.py          # IEEE Xplore API integration
├── getDBLP.py                # DBLP API integration
├── getWebOfScience.py        # Web of Science API integration
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (create this)
└── README.md                # This file
```

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.env` file with your API keys:
```bash
# Scopus API key
APIKey=your_scopus_api_key_here

# IEEE Xplore API key
IEEEXploreAPIKey=your_ieee_api_key_here

# DBLP API (no key required - free service)

# Web of Science API key
WebOfScienceAPIKey=your_wos_api_key_here
```

## Usage

### Scopus API

```python
from getScopus import get_scopus

# Search for papers in a specific journal
papers_df = get_scopus(
    issn='0317-7173',  # Journal ISSN
    query='(multivariate OR bivariate) AND (carto* OR visual* OR geovis*)',
    start_year=2010,
    end_year=2024,
    save_bibtex=True
)
```

### IEEE Xplore API

```python
from getIEEExplore import get_ieee_xplore

# Search for papers
papers_df = get_ieee_xplore(
    query='(multivariate OR bivariate) AND (carto* OR visual* OR geovis*)',
    start_year=2010,
    end_year=2024,
    save_bibtex=True
)
```

### DBLP API

```python
from getDBLP import get_dblp

# Search for papers (no API key required)
papers_df = get_dblp(
    query='(multivariate OR bivariate) AND (carto* OR visual* OR geovis*)',
    start_year=2010,
    end_year=2024,
    save_bibtex=True
)
```

### Web of Science API

```python
from getWebOfScience import get_web_of_science

# Search for papers
papers_df = get_web_of_science(
    query='(multivariate OR bivariate) AND (carto* OR visual* OR geovis*)',
    start_year=2010,
    end_year=2024,
    save_bibtex=True
)
```

## Output

Both APIs save results to:
- **CSV files**: `output/{source}/papers_{identifier}.csv`
- **BibTeX files**: `output/{source}/papers_{identifier}.bib` (optional)

## Shared Utilities

The `utils/api_utils.py` module provides common functionality:

- **API Error Handling**: Standardized exception classes
- **Response Validation**: Generic API response validation
- **Paper Processing**: Common paper entry validation and cleaning
- **File Operations**: CSV and BibTeX file generation
- **BibTeX Generation**: Clean BibTeX key generation and formatting

## API Keys

### Scopus API
- Get your API key from [Elsevier Developer Portal](https://dev.elsevier.com/)
- Set as `APIKey` in your `.env` file

### IEEE Xplore API
- Get your API key from [IEEE Xplore Developer Portal](https://developer.ieee.org/)
- Set as `IEEEXploreAPIKey` in your `.env` file

### DBLP API
- **No API key required** - DBLP provides free access to their API
- Available at [DBLP API](https://dblp.org/faq/13501472.html)
- Focuses on computer science publications

### Web of Science API
- Get your API key from [Clarivate Analytics Developer Portal](https://developer.clarivate.com/)
- Set as `WebOfScienceAPIKey` in your `.env` file
- Comprehensive coverage across all disciplines
- High-quality citation data and impact metrics

## Features

- **Rate Limiting**: Automatic handling of API rate limits
- **Error Recovery**: Retry logic with exponential backoff
- **Data Deduplication**: Removes duplicate papers by DOI
- **Flexible Output**: CSV and BibTeX formats
- **Comprehensive Logging**: Detailed progress and error logging
- **Input Validation**: Robust parameter validation

## Error Handling

The system handles various error types:
- **Rate Limit Errors**: Automatic retry with delays
- **Network Errors**: Retry with exponential backoff
- **API Errors**: Proper error messages and logging
- **Validation Errors**: Clear error messages for invalid inputs

## Contributing

To add support for new APIs:
1. Create a new file (e.g., `getNewAPI.py`)
2. Import shared utilities from `utils.api_utils`
3. Implement API-specific validation functions
4. Follow the same pattern as existing implementations 