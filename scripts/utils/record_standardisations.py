from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
from sklearn.metrics.pairwise import cosine_similarity  # type: ignore
import pandas as pd

def standardize_records(records):
    """Standardize records from different sources"""
    standardized = []
    
    for record in records:
        std_record = {
            'title': record.get('title', ''),
            'authors': record.get('authors', []),
            'year': record.get('year', ''),
            'journal': record.get('journal', ''),
            'doi': record.get('doi', ''),
            'abstract': record.get('abstract', ''),
            'source': record.get('source', ''),
            'url': record.get('url', '')
        }
        standardized.append(std_record)
    
    return pd.DataFrame(standardized)

def remove_duplicates(df):
    """Remove duplicate records based on title similarity"""

    # Create TF-IDF vectors for titles
    vectorizer = TfidfVectorizer(stop_words='english', ngram_range=(1, 2))
    tfidf_matrix = vectorizer.fit_transform(df['title'].fillna(''))
    
    # Calculate similarity matrix
    similarity_matrix = cosine_similarity(tfidf_matrix)
    
    # Find duplicates (similarity > 0.8)
    duplicates = []
    for i in range(len(similarity_matrix)):
        for j in range(i+1, len(similarity_matrix)):
            if similarity_matrix[i][j] > 0.8:
                duplicates.append(j)
    
    # Remove duplicates
    return df.drop(df.index[duplicates]).reset_index(drop=True)


def screen_titles_abstracts(df, inclusion_criteria, exclusion_criteria):
    """Screen titles and abstracts using keyword matching"""

    def check_criteria(text, criteria, include=True):
        text_lower = text.lower()
        for criterion in criteria:
            if isinstance(criterion, str):
                if criterion.lower() in text_lower:
                    return include
            elif isinstance(criterion, list):  # AND criteria
                if all(term.lower() in text_lower for term in criterion):
                    return include
        return not include

    # Apply screening
    df['include_title'] = df['title'].apply(
        lambda x: check_criteria(x, inclusion_criteria, True)
    )
    df['exclude_title'] = df['title'].apply(
        lambda x: check_criteria(x, exclusion_criteria, False)
    )

    # Combine title and abstract screening
    df['passed_screening'] = (df['include_title'] & ~df['exclude_title'])

    return df

# Example criteria
# inclusion_criteria = [
#     ['machine learning', 'healthcare'],
#     ['artificial intelligence', 'medical'],
#     ['deep learning', 'clinical']
# ]

# exclusion_criteria = [
#     'review article',
#     'systematic review',
#     'meta-analysis',
#     'conference abstract'
# ]