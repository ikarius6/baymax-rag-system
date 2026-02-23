import pandas as pd
import json
import os
from tqdm.auto import tqdm
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
import chromadb
from utils import import_csv, clean_data_schema
import chromadb

load_dotenv(override=True)

# --- Configuration ---
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "32"))
COLLECTION_NAME = "confluence_docs"
CHROMA_DB_PATH = "./chroma_db"
CSV_PATH = "./data/kb.csv"
GITHUB_CSV_PATH = "./data/github.csv"
MAX_ROWS = 2000

import torch


def check_gpu_compatibility():
    """Check if PyTorch supports your GPU."""
    if not torch.cuda.is_available():
        print("CUDA not available — running on CPU (will be slow)")
        return "cpu"

    cc = torch.cuda.get_device_capability()
    gpu_name = torch.cuda.get_device_name()
    pytorch_version = torch.__version__

    print(f"GPU: {gpu_name}")
    print(f"Compute Capability: sm{cc[0]}{cc[1]}")
    print(f"PyTorch: {pytorch_version}")
    print(f"CUDA: {torch.version.cuda}")

    if cc[0] >= 9:
        version_parts = pytorch_version.split("+")[0].split(".")
        major_minor = float(f"{version_parts[0]}.{version_parts[1]}")
        if major_minor >= 2.9 or "dev" in pytorch_version or "nightly" in pytorch_version:
            print("Blackwell-compatible PyTorch detected")
        else:
            print(f"\nPyTorch {pytorch_version} may not fully support Blackwell")
            print("Consider: pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu128")

    return "cuda"


DEVICE = check_gpu_compatibility()
print(f"Loading embedding model: {EMBED_MODEL_NAME} (device: {DEVICE})")
embed_model = SentenceTransformer(EMBED_MODEL_NAME, device=DEVICE)


def batch_generate_embeddings(texts, model, batch_size=32):
    """Generate embeddings in batches for efficiency."""
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    return embeddings.tolist()


def main():
    import sys
    full_reindex = "--full" in sys.argv

    # --- Load and clean data ---
    print("Loading CSV data...")
    
    # We'll just define an empty DataFrame with the required schema to pass into import_csv
    columns = ['id', 'type', 'status', 'tiny_link', 'title', 'content', 'is_internal', 'last_modified']
    df = pd.DataFrame(columns=columns)

    for csv_path in [CSV_PATH, GITHUB_CSV_PATH]:
        if os.path.exists(csv_path):
            df = import_csv(df, csv_path, MAX_ROWS)

    if df is None or (isinstance(df, str) and df.startswith("Error")):
         print(f"Data load error: {df}")
         return
    if df.empty:
        print("No data found to index.")
        return

    # Clean the dataset, handles NaN, normalizes IDs to str, extracts 'metadata' with 'source', 'text', 'type'
    df = clean_data_schema(df)
    
    # After clean_data_schema, the dataframe only has 'id' and 'metadata' columns
    # We'll extract 'content' from the 'metadata' to pass into our batch generator
    df['content'] = df['metadata'].apply(lambda m: json.loads(m)['text'])

    print(f"Loaded a total of {len(df)} documents with content.")

    # --- ChromaDB setup ---
    os.makedirs(CHROMA_DB_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    existing_collections = [c.name for c in client.list_collections()]
    if COLLECTION_NAME not in existing_collections:
        print(f"Creating collection: {COLLECTION_NAME}")
        client.create_collection(name=COLLECTION_NAME)
    else:
        print(f"Collection '{COLLECTION_NAME}' already exists.")

    col = client.get_collection(COLLECTION_NAME)

    # --- Incremental: skip already-indexed documents ---
    if not full_reindex:
        try:
            existing = col.get(include=[])
            existing_ids = set(existing["ids"])
            new_df = df[~df["id"].isin(existing_ids)]
            if len(new_df) == 0:
                print("All documents already indexed. Nothing to do.")
                print("   Use --full to force re-embedding all documents.")
                return
            print(f"Incremental mode: {len(new_df)} new documents (skipping {len(df) - len(new_df)} already indexed)")
            print("   Use --full to force re-embedding all documents.")
            df = new_df
        except Exception:
            print("First indexing run — processing all documents.")
    else:
        print("Full re-index mode (--full flag)")

    # --- Generate embeddings in batch ---
    texts = df["content"].tolist()
    print(f"Generating embeddings with batch_size={BATCH_SIZE}...")
    embeddings = batch_generate_embeddings(texts, embed_model, batch_size=BATCH_SIZE)
    df = df.copy()
    df["embeddings"] = embeddings

    # --- Prepare metadata ---
    # The clean_data_schema generates 'metadata' as a JSON string
    # Chroma requires metadata to be a dictionary, so we parse it back
    df["metadata_dict"] = df["metadata"].apply(json.loads)

    # --- Upsert to ChromaDB ---
    ids = df["id"].tolist()
    documents = df["content"].tolist()
    metadatas = df["metadata_dict"].tolist()
    embs = df["embeddings"].tolist()

    for i in tqdm(range(0, len(ids), 200), desc="Upserting to ChromaDB"):
        end = min(i + 200, len(ids))
        col.upsert(
            ids=ids[i:end],
            documents=documents[i:end],
            metadatas=metadatas[i:end],
            embeddings=embs[i:end],
        )

    print(f"Done! Indexed {len(ids)} documents into '{COLLECTION_NAME}'.")


if __name__ == "__main__":
    main()
