import requests
import pandas as pd
import base64
from dotenv import load_dotenv
from tqdm import tqdm
from bs4 import BeautifulSoup
import markdown
import os
import sys
import json
from datetime import datetime, timezone

load_dotenv(override=True)
github_token = os.getenv("GITHUB_TOKEN")
github_org_name = os.getenv("GITHUB_ORG_NAME")
github_url = os.getenv("GITHUB_URL")

headers = {
  'Authorization': f'token {github_token}',
  'Accept': 'application/vnd.github.v3+json',
}

def create_dataframe():
  try:
    columns = ['id', 'type', 'status', 'tiny_link', 'title', 'content', 'is_internal', 'last_modified']
    df = pd.DataFrame(columns=columns)
    return df
  except Exception as e:
    print(f"An error occurred while creating the DataFrame: {e}")
    return None

def set_index_of_dataframe(df):
  if not isinstance(df, pd.DataFrame):
    print("Error: The argument must be a pandas DataFrame.")
    return None

  if 'name' not in df.columns:
    print("Error: 'name' column not found in the DataFrame.")
    return None

  try:
    df.set_index('name', inplace=True)
    return df
  except Exception as e:
    print(f"An error occurred while setting the index: {e}")
    return None
      
def add_all_pages_to_dataframe(df, all_pages):
  if not isinstance(df, pd.DataFrame):
    print("Error: The first argument must be a pandas DataFrame.")
    return None

  if not isinstance(all_pages, list):
    print("Error: The second argument must be a list.")
    return None

  for repo in all_pages:
    try:
      new_record = [{
          'id': repo.get('node_id', ''),
          'name': repo.get('name', ''),
          'type': 'repo',
          'status': 'current',
          'tiny_link': repo.get('html_url', ''),
          'title': repo.get('full_name', ''),
          'last_modified': repo.get('updated_at', '')
      }]

      # Add new records to the DataFrame
      df = pd.concat([df, pd.DataFrame(new_record)], ignore_index=True)
    except Exception as e:
      print(f"An error occurred while adding a page to the DataFrame: {e}")

  return df

def fetch_repo_content(repo_name):
    readme_url = f'{github_url}/api/v3/repos/{github_org_name}/{repo_name}/readme'
    readme_response = requests.get(readme_url, headers=headers)

    if readme_response.status_code == 200:
      try:
        content = readme_response.json()['content']
        content = base64.b64decode(content)
        return content
      except KeyError:
        print("Error: Unable to access page content in the returned JSON.")
        return None
    else:
      print("Failed to fetch page content.")
      return None

def add_content_to_dataframe(df):
  # Check if the input is a pandas DataFrame
  if not isinstance(df, pd.DataFrame):
    print("Error: The variable 'df' must be a pandas DataFrame.")
    return df

  # Wrap the loop in tqdm for progress tracking
  for name, row in tqdm(df.iterrows(), total=df.shape[0], desc="Updating DataFrame"):
    markdown_content = fetch_repo_content(name)

    if markdown_content is not None:
        try:
          # Convert Markdown to HTML
          html_content = markdown.markdown(markdown_content)

          # Parse the HTML content
          soup = BeautifulSoup(html_content, "lxml")

          # Extract text with proper spacing
          text_parts = []
          for element in soup.stripped_strings:
              text_parts.append(element)

          page_content = ' '.join(text_parts)

          # Update the DataFrame with the extracted content
          df.loc[name, 'content'] = page_content
        except Exception as e:
          print(f"Error processing HTML content for repo NAME {name}: {e}")
    else:
      print(f"Warning: Could not fetch content for repo NAME {name}.")

    # Add a delay between requests to avoid hitting rate limits
    # time.sleep(1)

  return df

def fetch_all_repositories():
  # Get list of repositories
  repos_url = f'{github_url}/api/v3/orgs/{github_org_name}/repos?per_page=999'
  all_repos = requests.get(repos_url, headers=headers).json()
  return all_repos

def save_dataframe_to_csv(df, filename):
  if not isinstance(df, pd.DataFrame):
    print("Error: The variable 'df' must be a pandas DataFrame.")
  else:
    try:
      os.makedirs('./data', exist_ok=True)
      df.to_csv(filename, index=False)
      print("Data successfully saved " + str(len(df)) + " records to " + filename)
    except Exception as e:
      print(f"An error occurred while saving the DataFrame to CSV: {e}")


SYNC_STATE_FILE = './data/.github_last_sync'

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
    csv_file = './data/github.csv'
    full_sync = '--full' in sys.argv
    last_sync = None if full_sync else load_sync_state()

    if full_sync:
        print("Full sync mode (--full flag)")
    elif last_sync:
        print(f"Incremental sync (changes since {last_sync})")
    else:
        print("First run — full sync")
    
    print(f"Fetching all repositories under '{github_org_name}'...")
    all_pages = fetch_all_repositories()

    print(f"Total repositories fetched: {len(all_pages)}")
    df = create_dataframe()
    df = add_all_pages_to_dataframe(df, all_pages)
    df = set_index_of_dataframe(df)

    # Incremental: filter to only new/modified repositories
    if last_sync and not full_sync:
        existing_df = None
        if os.path.exists(csv_file):
            existing_df = pd.read_csv(csv_file, index_col='name')

        changed_mask = df['last_modified'].apply(
            lambda x: x > last_sync if pd.notna(x) and x else True
        )
        changed_ids = set(df[changed_mask].index)
        new_ids = set(df.index) - set(existing_df.index) if existing_df is not None else set(df.index)
        pages_to_process = changed_ids | new_ids

        if not pages_to_process:
            print("No new or modified repositories found. Nothing to sync.")
            save_sync_state()
            return

        print(f"Processing {len(pages_to_process)} new/modified repositories (skipping {len(df) - len(pages_to_process)} unchanged)")

        # Only fetch content for changed repositories
        df_to_fetch = df.loc[df.index.isin(pages_to_process)]
        df_to_fetch = add_content_to_dataframe(df_to_fetch)

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
        df = add_content_to_dataframe(df)

    # Reset index so 'name' becomes a column again for the CSV save, similar to the original setup
    df.reset_index(inplace=True)
    save_dataframe_to_csv(df, csv_file)
    save_sync_state()
    print(f"Sync complete. State saved to {SYNC_STATE_FILE}")

if __name__ == "__main__":
    main()