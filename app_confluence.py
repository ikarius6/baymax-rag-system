import requests
import os
from dotenv import load_dotenv
import pandas as pd
from bs4 import BeautifulSoup
from tqdm import tqdm

# Load environment variables from .env file
load_dotenv()
confluence_domain = os.getenv("CONFLUENCE_DOMAIN")
confluence_token = os.getenv("CONFLUENCE_TOKEN")
space_key = os.getenv("CONFLUENCE_SPACE_KEY")
team_key = os.getenv("CONFLUENCE_TEAM_KEY")

# Get cookie.txt with the session to avoid SSO issues
with open('cookie.txt', 'r') as file:
    cookie = file.read()

# Function to make an API call
def api_call(url):
    try:
        response = requests.get(url, headers={'Authorization': "Bearer " + confluence_token, 'Cookie': cookie})
        response.raise_for_status()
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            print("Error: Page not found.")
        elif response.status_code == 401:
            print("Error: Authentication failed.")
        elif response.status_code == 500:
            print("Error: Internal server error.")
        else:
            print(f"Failed to get pages: HTTP status code {response.status_code}")
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
    url = f'{confluence_domain}/rest/api/content/{page_id}/child/page?limit=999'
    json_data = api_call(url)
    if json_data:
        return json_data.get('results', [])
    else:
        print(f"Error: Could not fetch child pages for page ID {page_id}.")
        return []

# Function to create an empty DataFrame    
def create_dataframe():
    try:
        columns = ['id', 'type', 'status', 'tiny_link', 'title', 'content', 'is_internal']
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
            new_record = [{
                'id': page.get('id', ''),
                'type': page.get('type', ''),
                'status': page.get('status', ''),
                'tiny_link': page.get('_links', {}).get('tinyui', ''),
                'title': page.get('title', '')
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

def add_content_to_dataframe(df):
    # Check if the input is a pandas DataFrame
    if not isinstance(df, pd.DataFrame):
        print("Error: The variable 'df' must be a pandas DataFrame.")
        return df

    # Wrap the loop in tqdm for progress tracking
    for page_id, row in tqdm(df.iterrows(), total=df.shape[0], desc="Updating DataFrame"):
        html_content = fetch_page_content(page_id)

        if html_content is not None:
            try:
                # Parse the HTML content
                soup = BeautifulSoup(html_content, "lxml")

                # Extract text with proper spacing
                text_parts = []
                for element in soup.stripped_strings:
                    text_parts.append(element)

                page_content = ' '.join(text_parts)

                # Update the DataFrame with the extracted content
                df.loc[page_id, 'content'] = page_content
            except Exception as e:
                print(f"Error processing HTML content for page ID {page_id}: {e}")
        else:
            print(f"Warning: Could not fetch content for page ID {page_id}.")

    return df

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

def fetch_all_pages_recursively(page_id, all_pages):
    child_pages = fetch_child_pages(page_id)
    for page in child_pages:
        all_pages.append(page)
        fetch_all_pages_recursively(page['id'], all_pages)
    return all_pages

def main():
    csv_file = './data/kb.csv'
    
    print(f"Fetching '{team_key}' page ID...")
    team_page_id = fetch_team_page_id()
    if not team_page_id:
        print(f"Failed to fetch '{team_key}' page ID. Exiting.")
        return
    
    print(f"Fetching all pages under '{team_key}'...")
    all_pages = fetch_all_pages_recursively(team_page_id, [])

    print(f"Total pages fetched: {len(all_pages)}")
    df = create_dataframe()
    df = add_all_pages_to_dataframe(df, all_pages)
    df = set_index_of_dataframe(df)
    df = delete_internal_only_records(df)
    print("Removed internal_only records")
    print("Adding content to DataFrame...")
    df = add_content_to_dataframe(df)
    save_dataframe_to_csv(df, csv_file)

if __name__ == "__main__":
    main()
