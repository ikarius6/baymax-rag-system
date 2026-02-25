import requests
import os
import re
import sys
import json
from datetime import datetime, timezone
from urllib.parse import urlparse
from dotenv import load_dotenv
import pandas as pd
from bs4 import BeautifulSoup
from tqdm import tqdm
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pathlib import Path

# Load environment variables from .env file
load_dotenv(override=True)
confluence_domain = os.getenv("CONFLUENCE_DOMAIN")
confluence_token = os.getenv("CONFLUENCE_TOKEN")
space_key = os.getenv("CONFLUENCE_SPACE_KEY")
team_key = os.getenv("CONFLUENCE_TEAM_KEY")

COOKIE_FILE = Path("cookie.txt")

def _load_cookies(session, domain):
    """Parse cookie.txt and load cookies into the session cookie jar."""
    if not COOKIE_FILE.exists():
        print(f"Warning: {COOKIE_FILE} not found — using Bearer token only.")
        return
    raw = COOKIE_FILE.read_text().strip()
    if not raw:
        print(f"Warning: {COOKIE_FILE} is empty — using Bearer token only.")
        return
    parsed = urlparse(domain)
    cookie_domain = parsed.hostname  # e.g. confluence.disney.com
    count = 0
    for pair in raw.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        session.cookies.set(name.strip(), value.strip(), domain=cookie_domain)
        count += 1
    print(f"Auth mode: cookie.txt ({count} cookies loaded for {cookie_domain})")

# Function to create a session with retry strategy
def create_session_with_retries():
    session = requests.Session()
    retry = Retry(
        total=5,  # Total number of retries
        backoff_factor=1,  # Exponential backoff factor
        status_forcelist=[429, 500, 502, 503, 504]  # HTTP status codes to retry on
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    _load_cookies(session, confluence_domain)
    return session

session = create_session_with_retries()

# Function to make an API call
def api_call(url):
    headers = {"Accept": "application/json"}

    try:
        response = session.get(url, headers=headers, allow_redirects=False, timeout=30)

        # SSO/auth failures often return redirects or HTML pages (not JSON).
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location", "<missing>")
            print(f"Redirected ({response.status_code}) to {location}")
            return None

        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "")
            if "application/json" not in content_type.lower():
                snippet = response.text[:250].replace("\n", " ")
                print(
                    "Unexpected non-JSON response: "
                    f"status={response.status_code}, content-type={content_type}, "
                    f"url={response.url}, body[:250]={snippet!r}"
                )
                return None
            try:
                return response.json()
            except ValueError:
                snippet = response.text[:250].replace("\n", " ")
                print(
                    "Invalid JSON payload: "
                    f"status={response.status_code}, content-type={content_type}, "
                    f"url={response.url}, body[:250]={snippet!r}"
                )
                return None
        elif response.status_code == 404:
            print("Error: Page not found.")
        elif response.status_code == 401:
            print("Error: Authentication failed.")
        elif response.status_code == 403:
            print("Error: Forbidden (missing permission or invalid SSO session).")
        elif response.status_code == 500:
            print("Error: Internal server error.")
        else:
            print(f"Failed to get pages: HTTP status code {response.status_code} (url={response.url})")
    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")

    return None

# Function to fetch the team_key page ID
def fetch_team_page_id():
    url = f'{confluence_domain}/rest/api/content?spaceKey={space_key}&title={team_key}'
    json_data = api_call(url)
    if json_data and json_data.get('results'):
        return json_data['results'][0]['id']
    else:
        print(f"Error: '{team_key}' page not found.")
        return None

# Function to fetch child pages
def fetch_child_pages(page_id):
    url = f'{confluence_domain}/rest/api/content/{page_id}/child/page?limit=999&expand=version'
    json_data = api_call(url)
    if json_data:
        return json_data.get('results', [])
    else:
        print(f"Warning: Could not fetch child pages for page ID {page_id}.")
        return []

# Function to create an empty DataFrame    
def create_dataframe():
    try:
        columns = ['id', 'type', 'status', 'tiny_link', 'title', 'content', 'is_internal', 'parent_id', 'last_modified']
        df = pd.DataFrame(columns=columns)
        return df
    except Exception as e:
        print(f"An error occurred while creating the DataFrame: {e}")
        return None

# Function to add all pages to the DataFrame
def add_all_pages_to_dataframe(df, all_pages):
    if not isinstance(df, pd.DataFrame):
        print("Error: The first argument must be a pandas DataFrame.")
        return None

    if not isinstance(all_pages, list):
        print("Error: The second argument must be a list.")
        return None

    for page in all_pages:
        try:
            last_modified = page.get('version', {}).get('when', '')
            new_record = [{
                'id': page.get('id', ''),
                'type': page.get('type', 'page'),
                'status': page.get('status', ''),
                'tiny_link': page.get('_links', {}).get('tinyui', ''),
                'title': page.get('title', ''),
                'parent_id': page.get('parent_id', ''),
                'last_modified': last_modified
            }]

            # Add new records to the DataFrame
            df = pd.concat([df, pd.DataFrame(new_record)], ignore_index=True)
        except Exception as e:
            print(f"An error occurred while adding a page to the DataFrame: {e}")

    return df

# Function to set index of the DataFrame
def set_index_of_dataframe(df):
    if not isinstance(df, pd.DataFrame):
        print("Error: The argument must be a pandas DataFrame.")
        return None

    if 'id' not in df.columns:
        print("Error: 'id' column not found in the DataFrame.")
        return None

    try:
        df.set_index('id', inplace=True)
        return df
    except Exception as e:
        print(f"An error occurred while setting the index: {e}")
        return None

# Function to fetch labels from Confluence
def fetch_labels(page_id):
    url = f'{confluence_domain}/rest/api/content/{page_id}/label'
    json_data = api_call(url)

    if json_data:
        try:
            internal_only = False
            for item in json_data.get("results", []):
                if item.get("name") == 'internal_only':
                    internal_only = True

            return internal_only
        except KeyError:
            print("Error processing JSON data.")
            return None
    else:
        print("Failed to fetch labels.")
        return None

# Function to fetch page content from Confluence
def fetch_page_content(page_id):
    url = f'{confluence_domain}/rest/api/content/{page_id}?expand=body.storage'
    json_data = api_call(url)

    if json_data:
        try:
            return json_data['body']['storage']['value']
        except KeyError:
            print("Error: Unable to access page content in the returned JSON.")
            return None
    else:
        print("Failed to fetch page content.")
        return None

# Function to delete internal_only records
def delete_internal_only_records(df):
    # Ensure df is a pandas DataFrame
    if not isinstance(df, pd.DataFrame):
        print("Error: The variable 'df' must be a pandas DataFrame.")
        return df
    
    # Loop through the DataFrame with a tqdm progress bar
    if 'is_internal' in df.columns:
        for page_id, row in tqdm(df.iterrows(), total=df.shape[0], desc="Updating is_internal status"):
            is_internal_page = fetch_labels(page_id)
            
            if is_internal_page is not None:
                df.loc[page_id, 'is_internal'] = is_internal_page
            else:
                print(f"Warning: Could not fetch labels for page ID {page_id}.")
    else:
        print("Error: 'is_internal' column not found in the DataFrame.")
        return df
    
    # Delete internal_only records
    df = df[df['is_internal'] != True]

    return df

def extract_cross_page_links(html_content, source_page_id, all_page_ids):
    """Extract links from HTML that point to other Confluence pages in the same space."""
    links = []
    if not html_content:
        return links
    try:
        soup = BeautifulSoup(html_content, "lxml")
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            # Match Confluence internal links: /display/SPACE/Title or /pages/viewpage.action?pageId=XXXX
            page_id_match = re.search(r'pageId=(\d+)', href)
            if page_id_match:
                target_id = page_id_match.group(1)
                if target_id in all_page_ids and target_id != str(source_page_id):
                    links.append({'source_id': str(source_page_id), 'target_id': target_id})
            # Also match ri:content-title links (Confluence storage format)
            for link_tag in soup.find_all('ri:page', attrs={'ri:content-title': True}):
                title = link_tag.get('ri:content-title', '')
                links.append({'source_id': str(source_page_id), 'target_title': title})
    except Exception as e:
        print(f"Warning: Could not extract links from page {source_page_id}: {e}")
    return links

def add_content_to_dataframe(df, all_page_ids=None):
    """Fetch page content from Confluence and extract cross-page links."""
    if not isinstance(df, pd.DataFrame):
        print("Error: The variable 'df' must be a pandas DataFrame.")
        return df, []

    if all_page_ids is None:
        all_page_ids = set(str(pid) for pid in df.index)

    all_links = []

    for page_id, row in tqdm(df.iterrows(), total=df.shape[0], desc="Fetching page content"):
        html_content = fetch_page_content(page_id)

        if html_content is not None:
            try:
                soup = BeautifulSoup(html_content, "lxml")

                text_parts = []
                for element in soup.stripped_strings:
                    text_parts.append(element)

                page_content = ' '.join(text_parts)
                df.loc[page_id, 'content'] = page_content

                # Extract cross-page links
                page_links = extract_cross_page_links(html_content, page_id, all_page_ids)
                all_links.extend(page_links)
            except Exception as e:
                print(f"Error processing HTML content for page ID {page_id}: {e}")
        else:
            print(f"Warning: Could not fetch content for page ID {page_id}.")

        # Add a delay between requests to avoid hitting rate limits
        # time.sleep(1)

    return df, all_links

def save_dataframe_to_csv(df, filename):
    if not isinstance(df, pd.DataFrame):
        print("Error: The variable 'df' must be a pandas DataFrame.")
    else:
        try:
            os.makedirs('./data', exist_ok=True)
            df.to_csv(filename, index=True)
            print("Data successfully saved " + str(len(df)) + " records to " + filename)
        except Exception as e:
            print(f"An error occurred while saving the DataFrame to CSV: {e}")

def save_hierarchy_csv(df, filename='./data/page_hierarchy.csv'):
    """Save page parent-child hierarchy to CSV."""
    if not isinstance(df, pd.DataFrame) or 'parent_id' not in df.columns:
        print("Error: DataFrame must have a 'parent_id' column.")
        return
    hierarchy = df[['parent_id']].copy()
    hierarchy.index.name = 'child_id'
    hierarchy['title'] = df['title'] if 'title' in df.columns else ''
    os.makedirs('./data', exist_ok=True)
    hierarchy.to_csv(filename, index=True)
    print(f"Hierarchy saved ({len(hierarchy)} records) to {filename}")

def save_links_csv(links, filename='./data/page_links.csv'):
    """Save extracted cross-page links to CSV."""
    if not links:
        print("No cross-page links found.")
        return
    links_df = pd.DataFrame(links)
    os.makedirs('./data', exist_ok=True)
    links_df.to_csv(filename, index=False)
    print(f"Cross-page links saved ({len(links_df)} records) to {filename}")

def fetch_all_pages_recursively(page_id, all_pages, parent_id=None):
    """Recursively fetch child pages, tracking parent_id for hierarchy."""
    child_pages = fetch_child_pages(page_id)
    for page in child_pages:
        page['parent_id'] = str(page_id)
        all_pages.append(page)
        fetch_all_pages_recursively(page['id'], all_pages, page_id)
    return all_pages

SYNC_STATE_FILE = './data/.last_sync'

def load_sync_state():
    """Load the last sync timestamp."""
    if os.path.exists(SYNC_STATE_FILE):
        with open(SYNC_STATE_FILE, 'r') as f:
            data = json.load(f)
            return data.get('last_sync', None)
    return None

def save_sync_state():
    """Save the current sync timestamp."""
    os.makedirs('./data', exist_ok=True)
    with open(SYNC_STATE_FILE, 'w') as f:
        json.dump({'last_sync': datetime.now(timezone.utc).isoformat()}, f)

def main():
    csv_file = './data/kb.csv'
    full_sync = '--full' in sys.argv
    last_sync = None if full_sync else load_sync_state()

    if full_sync:
        print("Full sync mode (--full flag)")
    elif last_sync:
        print(f"Incremental sync (changes since {last_sync})")
    else:
        print("First run — full sync")

    print(f"Fetching '{team_key}' page ID...")
    team_page_id = fetch_team_page_id()
    if not team_page_id:
        print(f"Failed to fetch '{team_key}' page ID. Exiting.")
        return
    
    print(f"Team page ID: {team_page_id}")

    sys.exit(0)   

    print(f"Fetching all pages under '{team_key}'...")
    all_pages = fetch_all_pages_recursively(team_page_id, [])
    print(f"Total pages fetched: {len(all_pages)}")

    # Build full page list DataFrame
    df = create_dataframe()
    df = add_all_pages_to_dataframe(df, all_pages)
    df = set_index_of_dataframe(df)

    # Incremental: filter to only new/modified pages
    if last_sync and not full_sync:
        existing_df = None
        if os.path.exists(csv_file):
            existing_df = pd.read_csv(csv_file, index_col='id', dtype={'id': str})
            existing_df.index = existing_df.index.astype(str)

        changed_mask = df['last_modified'].apply(
            lambda x: x > last_sync if pd.notna(x) and x else True
        )
        changed_ids = set(df[changed_mask].index)
        new_ids = set(df.index) - set(existing_df.index) if existing_df is not None else set(df.index)
        pages_to_process = changed_ids | new_ids

        if not pages_to_process:
            print("No new or modified pages found. Nothing to sync.")
            save_sync_state()
            return

        print(f"Processing {len(pages_to_process)} new/modified pages (skipping {len(df) - len(pages_to_process)} unchanged)")

        # Only fetch content for changed pages
        df_to_fetch = df.loc[df.index.isin(pages_to_process)]
        all_page_ids = set(str(pid) for pid in df.index)
        df_to_fetch, cross_links = add_content_to_dataframe(df_to_fetch, all_page_ids)

        # Merge with existing data
        if existing_df is not None:
            existing_df.update(df_to_fetch)
            new_pages = df_to_fetch.loc[~df_to_fetch.index.isin(existing_df.index)]
            df = pd.concat([existing_df, new_pages])
        else:
            df = df_to_fetch
    else:
        # Full sync: fetch all content
        print("Adding content to DataFrame...")
        all_page_ids = set(str(pid) for pid in df.index)
        df, cross_links = add_content_to_dataframe(df, all_page_ids)

    save_dataframe_to_csv(df, csv_file)

    # Save hierarchy and cross-page links for GraphRAG
    save_hierarchy_csv(df)
    save_links_csv(cross_links)
    save_sync_state()
    print(f"Sync complete. State saved to {SYNC_STATE_FILE}")

if __name__ == "__main__":
    main()
