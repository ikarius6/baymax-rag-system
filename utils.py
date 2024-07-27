import pandas as pd
import json
from tqdm.auto import tqdm
import os
import ast
import chromadb
from langchain_chroma import Chroma
import textwrap

# Function to get dataset
def import_csv(df, csv_file, max_rows):
    print("Start: Getting dataset")

    # Check if file exists
    if not os.path.exists(csv_file):
        return "Error: CSV file does not exist."
    
    try:
        # Attempt to read the CSV file
        df = pd.read_csv(csv_file, usecols=['id', 'tiny_link', 'content'], nrows=max_rows)
    except FileNotFoundError:
        return "Error: CSV file not found."
    except PermissionError:
        return "Error: Permission denied when accessing the CSV file."
    except Exception as e:
        return f"Error: An unexpected error occurred while reading the CSV file. ({e})"
    
    # Check if DataFrame is empty
    if df.empty:
        return "Error: No data found in the CSV file."
    
    return df

def clean_data_schema(df):
    # Ensure necessary columns are present
    required_columns = {'id', 'tiny_link', 'content'}
    if not required_columns.issubset(df.columns):
        missing_columns = required_columns - set(df.columns)
        return f"Error: CSV file is missing required columns: {missing_columns}"
    
    # Filter out rows where 'content' is empty
    df = df[df['content'].notna() & (df['content'] != '')]
    
    if df.empty:
        return "Error: No valid data found in the CSV file after filtering empty content."
    
    # Proceed with the function's main logic
    #df['id'] = df['id'].astype(str)
    df = df.copy()
    df['id'] = df['id'].apply(str)
    df.rename(columns={'tiny_link': 'source'}, inplace=True)
    df['metadata'] = df.apply(lambda row: json.dumps({'source': row['source'], 'text': row['content']}), axis=1)
    df = df[['id', 'metadata']]
    # print(df.head())
    print("Done: Dataset retrieved")
    return df

def generate_embeddings_and_add_to_df(df, embed_model):
    print("Start: Generating embeddings and adding to DataFrame")
    # Check if the DataFrame and the 'metadata' column exist
    if df is None or 'metadata' not in df.columns:
        print("Error: DataFrame is None or missing 'metadata' column.")
        return None

    df['values'] = None

    for index, row in tqdm(df.iterrows(), total=df.shape[0]):
        try:
            content = row['metadata']
            meta = json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON for row {index}: {e}")
            continue  # Skip to the next iteration

        text = meta.get('text', '')
        if not text:
            print(f"Warning: Missing 'text' in metadata for row {index}. Skipping.")
            continue

        try:
            embedding = embed_model.embed_query(text)
            df.at[index, 'values'] = embedding
        except Exception as e:
            print(f"Error generating embedding for row {index}: {e}")

    print("Done: Generating embeddings and adding to DataFrame")
    return df

def upsert_data(df, col_name, chroma_client):
    print("Start: Upserting data to index")
    ids = []
    documents = []
    embeddings = []
    metadatas = []

    col = chroma_client.get_collection(col_name)

    for i, row in tqdm(df.iterrows(), total=df.shape[0]):
        metadata = ast.literal_eval(row['metadata'])
        document_id = row['id']
        content = row['metadata']
        meta = json.loads(content)
        document = meta.get('text', '')
        embedding = row.get('values',[])

        ids.append(document_id)
        metadatas.append(metadata)
        documents.append(document)
        embeddings.append(embedding)

        if len(ids) >= 200: # batching upserts
            col.upsert(documents=documents, ids=ids, metadatas=metadatas, embeddings=embeddings)
            ids = []
            documents = []
            metadatas = []
            embeddings = []

    # Upsert any remaining entries after the loop
    if len(ids) > 0:
        col.upsert(documents=documents, ids=ids, metadatas=metadatas, embeddings=embeddings)
    
    print("Done: Data upserted to index")
    return col

def get_chroma_vector_store(collection_name, embed_model, persist_dir):
    # Ensure the persist directory exists
    os.makedirs(persist_dir, exist_ok=True)
    
    # Initialize ChromaDB client with persistence settings
    client = chromadb.PersistentClient(path=persist_dir)

    existing_collections = client.list_collections()

    if collection_name not in [collection.name for collection in existing_collections]:
        print(f"Vector store {collection_name} does not exist, need to create it.")
        client.create_collection(name=collection_name)
        
    print(f"Vector store {collection_name} found.")
    vector_store = Chroma(
        client=client, 
        collection_name=collection_name, 
        embedding_function=embed_model
    )
    
    return vector_store, client

def get_embeddings(query, embed_model):
    embedding = embed_model.embed_query(query)
    print("Dimension of query embedding: ", len(embedding))
    return embedding

def wrap_text_preserve_newlines(text, width=110):
    # Split the input text into lines based on newline characters
    lines = text.split('\n')

    # Wrap each line individually
    wrapped_lines = [textwrap.fill(line, width=width) for line in lines]

    # Join the wrapped lines back together using newline characters
    wrapped_text = '\n'.join(wrapped_lines)

    return wrapped_text

def process_llm_response(llm_response):
    response = wrap_text_preserve_newlines(llm_response['result'])
    response += '\n\nSources:'
    for source in llm_response["source_documents"]:
        response += '\n'+ os.environ.get("CONFLUENCE_DOMAIN") + source.metadata['source']
    return response