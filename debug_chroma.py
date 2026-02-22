"""Quick diagnostic script to check what's in ChromaDB and test retrieval."""
import chromadb
import os
from dotenv import load_dotenv

load_dotenv(override=True)

CHROMA_DB_PATH = "./chroma_db"
COLLECTION_NAME = "confluence_docs"

client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
col = client.get_collection(COLLECTION_NAME)

total = col.count()
print(f"Total docs in ChromaDB: {total}")

# Check sample docs
sample = col.get(limit=3, include=["documents", "metadatas"])
print(f"\nSample IDs: {sample['ids'][:3]}")
for i, meta in enumerate(sample["metadatas"][:2]):
    print(f"\n--- Sample {i} metadata keys: {list(meta.keys())}")
    print(f"    source: {meta.get('source', 'N/A')}")
    text = meta.get("text", "")
    print(f"    text preview: {text[:200]}...")

# Search for 'mantis' in documents
print("\n\n=== Searching stored documents for 'mantis' ===")
all_docs = col.get(include=["documents", "metadatas"])
mantis_count = 0
for doc_id, doc, meta in zip(all_docs["ids"], all_docs["documents"], all_docs["metadatas"]):
    if doc and "mantis" in doc.lower():
        mantis_count += 1
        if mantis_count <= 5:
            print(f"  Found in doc {doc_id}: {doc[:150]}...")
print(f"\nTotal docs containing 'mantis': {mantis_count} out of {total}")

from langchain_community.embeddings.huggingface import HuggingFaceBgeEmbeddings

embed_model = HuggingFaceBgeEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cuda"},
    encode_kwargs={"normalize_embeddings": True},
)

# Test vector similarity search
print("\n\n=== Vector similarity search for 'Mantis DAG' ===")
query_embedding = embed_model.embed_query("tell me all you know about Mantis DAG")
print(f"Query embedding dimension: {len(query_embedding)}")

results = col.query(query_embeddings=[query_embedding], n_results=5, include=["documents", "metadatas", "distances"])
for i, (doc_id, doc, meta, dist) in enumerate(zip(results["ids"][0], results["documents"][0], results["metadatas"][0], results["distances"][0])):
    print(f"\n  Result {i+1} (distance={dist:.4f}):")
    print(f"    ID: {doc_id}")
    print(f"    source: {meta.get('source', 'N/A')}")
    has_mantis = "mantis" in (doc or "").lower()
    print(f"    contains 'mantis': {has_mantis}")
    print(f"    preview: {(doc or '')[:200]}...")
