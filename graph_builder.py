"""
graph_builder.py - Build a Neo4j knowledge graph from Confluence data.

Uses:
  - data/kb.csv for page nodes
  - data/page_hierarchy.csv for CHILD_OF relationships
  - data/page_links.csv for LINKS_TO relationships
  - Llama 4 Scout (via Groq or Ollama) for entity extraction

Usage:
  python graph_builder.py
"""

import os
import json
import pandas as pd
from tqdm import tqdm
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv(override=True)

# --- Configuration ---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")

KB_CSV = "./data/kb.csv"
GITHUB_CSV = "./data/github.csv"
HIERARCHY_CSV = "./data/page_hierarchy.csv"
LINKS_CSV = "./data/page_links.csv"

# Entity extraction model
ENTITY_MODEL = os.getenv("ENTITY_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

ENTITY_EXTRACTION_PROMPT = """You are an entity extractor. Given the following document content from a Confluence knowledge base, extract all notable entities and their relationships.

Return ONLY valid JSON with this structure:
{"entities": [{"name": "entity name", "type": "person|team|project|tool|process|service|concept"}], "relations": [{"source": "entity A", "target": "entity B", "relation": "relationship type"}]}

Rules:
- Extract people, teams, projects, tools, services, processes, and key concepts.
- Normalize entity names (e.g., "John D." and "John Doe" -> "John Doe").
- Use short, clear relation types like "owns", "uses", "part_of", "manages", "depends_on".
- If there are no entities, return {"entities": [], "relations": []}.
- Do NOT include any markdown formatting or explanation, ONLY the JSON.

Document content:
---
"""


def get_llm():
    """Get the LLM client for entity extraction (Groq or Ollama)."""
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        from groq import Groq
        return "groq", Groq(api_key=groq_key)
    else:
        import requests
        ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return "ollama", ollama_base


import re as _re
import time as _time


def _parse_json_response(text):
    """Robustly parse JSON from LLM response, handling markdown fences and fragments."""
    if not text or not text.strip():
        return {"entities": [], "relations": []}

    cleaned = text.strip()

    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        cleaned = _re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = _re.sub(r"\n?```\s*$", "", cleaned)

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object in the text
    match = _re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {"entities": [], "relations": []}


def extract_entities_groq(client, content, model=ENTITY_MODEL, _debug_count=[0]):
    """Extract entities using Groq API."""
    try:
        truncated = content[:4000] if len(content) > 4000 else content
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a JSON entity extractor. Always respond with valid JSON only, no markdown."},
                {"role": "user", "content": ENTITY_EXTRACTION_PROMPT + truncated + "\n---"},
            ],
            temperature=0,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content

        # Debug: print first successful response to verify format
        _debug_count[0] += 1
        if _debug_count[0] <= 2:
            print(f"  [DEBUG] Groq raw response (first {min(200, len(raw))} chars): {raw[:200]}")

        result = _parse_json_response(raw)

        if not result.get("entities") and _debug_count[0] <= 2:
            print(f"  [DEBUG] Parsed result has no entities. Full response: {raw[:500]}")

        return result

    except Exception as e:
        err_str = str(e)
        if "rate_limit" in err_str.lower() or "429" in err_str:
            print(f"  Rate limited, waiting 10s...")
            _time.sleep(10)
            return extract_entities_groq(client, content, model, _debug_count)
        print(f"  Groq extraction error: {err_str[:200]}")
        return {"entities": [], "relations": []}


def extract_entities_ollama(base_url, content, model=ENTITY_MODEL):
    """Extract entities using local Ollama."""
    import requests as req
    try:
        truncated = content[:6000] if len(content) > 6000 else content
        resp = req.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": ENTITY_EXTRACTION_PROMPT + truncated + "\n---"}],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return json.loads(resp.json()["message"]["content"])
    except Exception as e:
        print(f"  Ollama extraction error: {e}")
        return {"entities": [], "relations": []}


class GraphBuilder:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        print(f"Connected to Neo4j at {uri}")

    def close(self):
        self.driver.close()

    def clear_graph(self):
        """Remove all nodes and relationships."""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        print("Graph cleared.")

    def create_constraints(self):
        """Create uniqueness constraints for performance."""
        with self.driver.session() as session:
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (p:Page) REQUIRE p.page_id IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE")
        print("Constraints created.")

    def create_page_nodes(self, kb_csv):
        """Create Page nodes from kb.csv."""
        df = pd.read_csv(kb_csv)
        df = df[df["content"].notna()]
        df["id"] = df["id"].astype(str)

        with self.driver.session() as session:
            for _, row in tqdm(df.iterrows(), total=len(df), desc="Creating Page nodes"):
                preview = str(row["content"])[:500] if pd.notna(row["content"]) else ""
                session.run(
                    """
                    MERGE (p:Page {page_id: $page_id})
                    SET p.title = $title,
                        p.tiny_link = $tiny_link,
                        p.content_preview = $preview
                    """,
                    page_id=str(row["id"]),
                    title=str(row.get("title", "")),
                    tiny_link=str(row.get("tiny_link", "")),
                    preview=preview,
                )
        print(f"Created {len(df)} Page nodes.")
        return df

    def create_hierarchy_relationships(self, hierarchy_csv):
        """Create CHILD_OF relationships from page_hierarchy.csv."""
        if not os.path.exists(hierarchy_csv):
            print(f"Hierarchy file not found: {hierarchy_csv}. Skipping.")
            return

        df = pd.read_csv(hierarchy_csv)
        df["child_id"] = df["child_id"].astype(str)
        df["parent_id"] = df["parent_id"].astype(str)

        count = 0
        with self.driver.session() as session:
            for _, row in tqdm(df.iterrows(), total=len(df), desc="Creating CHILD_OF edges"):
                if row["parent_id"] and row["parent_id"] != "nan":
                    result = session.run(
                        """
                        MATCH (child:Page {page_id: $child_id})
                        MATCH (parent:Page {page_id: $parent_id})
                        MERGE (child)-[:CHILD_OF]->(parent)
                        RETURN count(*) as cnt
                        """,
                        child_id=str(row["child_id"]),
                        parent_id=str(row["parent_id"]),
                    )
                    count += result.single()["cnt"]
        print(f"Created {count} CHILD_OF relationships.")

    def create_link_relationships(self, links_csv):
        """Create LINKS_TO relationships from page_links.csv."""
        if not os.path.exists(links_csv):
            print(f"Links file not found: {links_csv}. Skipping.")
            return

        df = pd.read_csv(links_csv)
        count = 0
        with self.driver.session() as session:
            for _, row in tqdm(df.iterrows(), total=len(df), desc="Creating LINKS_TO edges"):
                if "target_id" in df.columns and pd.notna(row.get("target_id")):
                    result = session.run(
                        """
                        MATCH (src:Page {page_id: $source_id})
                        MATCH (tgt:Page {page_id: $target_id})
                        MERGE (src)-[:LINKS_TO]->(tgt)
                        RETURN count(*) as cnt
                        """,
                        source_id=str(row["source_id"]),
                        target_id=str(row["target_id"]),
                    )
                    count += result.single()["cnt"]
                elif "target_title" in df.columns and pd.notna(row.get("target_title")):
                    result = session.run(
                        """
                        MATCH (src:Page {page_id: $source_id})
                        MATCH (tgt:Page {title: $target_title})
                        MERGE (src)-[:LINKS_TO]->(tgt)
                        RETURN count(*) as cnt
                        """,
                        source_id=str(row["source_id"]),
                        target_title=str(row["target_title"]),
                    )
                    count += result.single()["cnt"]
        print(f"Created {count} LINKS_TO relationships.")

    def extract_and_store_entities(self, kb_csv, skip_existing=False):
        """Extract entities from page content using Llama 4 Scout and store in Neo4j."""
        df = pd.read_csv(kb_csv)
        df = df[df["content"].notna()]
        df["id"] = df["id"].astype(str)

        # Skip pages that already have entities extracted
        if skip_existing:
            with self.driver.session() as session:
                result = session.run(
                    "MATCH (e:Entity)-[:MENTIONED_IN]->(p:Page) RETURN DISTINCT p.page_id AS pid"
                )
                processed_ids = {r["pid"] for r in result}
            before = len(df)
            df = df[~df["id"].isin(processed_ids)]
            skipped = before - len(df)
            if skipped > 0:
                print(f"Skipping {skipped} already-processed pages, {len(df)} remaining.")
            if len(df) == 0:
                print("All pages already have entities extracted.")
                return

        llm_type, llm_client = get_llm()
        print(f"Using {llm_type} with model '{ENTITY_MODEL}' for entity extraction.")

        total_entities = 0
        total_relations = 0

        for _, row in tqdm(df.iterrows(), total=len(df), desc="Extracting entities"):
            content = str(row["content"])
            if len(content.strip()) < 50:
                continue

            if llm_type == "groq":
                result = extract_entities_groq(llm_client, content)
            else:
                result = extract_entities_ollama(llm_client, content)

            page_id = str(row["id"])

            with self.driver.session() as session:
                # Create Entity nodes and MENTIONED_IN relationships
                for entity in result.get("entities", []):
                    # Handle LLM returning plain strings instead of dicts
                    if isinstance(entity, str):
                        entity = {"name": entity, "type": "concept"}
                    if not isinstance(entity, dict):
                        continue
                    name = entity.get("name", "").strip()
                    etype = entity.get("type", "concept").strip().lower()
                    if not name:
                        continue

                    session.run(
                        """
                        MERGE (e:Entity {name: $name})
                        SET e.type = $type
                        WITH e
                        MATCH (p:Page {page_id: $page_id})
                        MERGE (e)-[:MENTIONED_IN]->(p)
                        """,
                        name=name,
                        type=etype,
                        page_id=page_id,
                    )
                    total_entities += 1

                # Create inter-entity relationships
                for rel in result.get("relations", []):
                    if not isinstance(rel, dict):
                        continue
                    src = rel.get("source", "").strip()
                    tgt = rel.get("target", "").strip()
                    rtype = rel.get("relation", "related_to").strip().upper().replace(" ", "_")
                    if not src or not tgt:
                        continue

                    session.run(
                        f"""
                        MERGE (a:Entity {{name: $src}})
                        MERGE (b:Entity {{name: $tgt}})
                        MERGE (a)-[:{rtype}]->(b)
                        """,
                        src=src,
                        tgt=tgt,
                    )
                    total_relations += 1

        print(f"Extracted {total_entities} entity mentions and {total_relations} inter-entity relations.")

    def assign_communities(self):
        """Assign community IDs to Page nodes using label propagation (Python fallback)."""
        print("Running community detection...")
        with self.driver.session() as session:
            # Fetch all pages and their connections
            result = session.run(
                """
                MATCH (p:Page)
                OPTIONAL MATCH (p)-[:CHILD_OF|LINKS_TO]-(q:Page)
                RETURN p.page_id AS page_id, collect(DISTINCT q.page_id) AS neighbors
                """
            )
            adjacency = {}
            for record in result:
                pid = record["page_id"]
                neighbors = [n for n in record["neighbors"] if n is not None]
                adjacency[pid] = neighbors

        # Simple label propagation
        labels = {pid: i for i, pid in enumerate(adjacency.keys())}
        for _ in range(10):  # max iterations
            changed = False
            for pid in adjacency:
                if not adjacency[pid]:
                    continue
                neighbor_labels = [labels.get(n, labels[pid]) for n in adjacency[pid]]
                most_common = max(set(neighbor_labels), key=neighbor_labels.count)
                if labels[pid] != most_common:
                    labels[pid] = most_common
                    changed = True
            if not changed:
                break

        # Store community IDs
        with self.driver.session() as session:
            for pid, community_id in labels.items():
                session.run(
                    "MATCH (p:Page {page_id: $pid}) SET p.community_id = $cid",
                    pid=pid,
                    cid=community_id,
                )

        num_communities = len(set(labels.values()))
        print(f"Assigned {num_communities} communities to {len(labels)} pages.")


def main():
    import sys
    full_rebuild = "--full" in sys.argv

    builder = GraphBuilder(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    try:
        if full_rebuild:
            print("Full rebuild mode (--full flag)")
            builder.clear_graph()
        else:
            print("Incremental mode (use --full to rebuild from scratch)")

        builder.create_constraints()

        data_sources = [KB_CSV, GITHUB_CSV]

        # Phase 1: Create/update page nodes (MERGE handles upserts)
        for src in data_sources:
            if os.path.exists(src):
                print(f"Processing page nodes from {src}...")
                builder.create_page_nodes(src)

        # Phase 2: Create structural relationships (MERGE handles duplicates)
        builder.create_hierarchy_relationships(HIERARCHY_CSV)
        builder.create_link_relationships(LINKS_CSV)

        # Phase 3: Extract entities — skip pages already processed
        for src in data_sources:
            if os.path.exists(src):
                print(f"Extracting entities from {src}...")
                builder.extract_and_store_entities(src, skip_existing=not full_rebuild)

        # Phase 4: Community detection
        builder.assign_communities()

        print("\nKnowledge graph built successfully!")
        print("Open Neo4j Browser at http://localhost:7474 to explore.")
        print("Try: MATCH (p:Page)-[r]->(q) RETURN p, r, q LIMIT 50")
    finally:
        builder.close()


if __name__ == "__main__":
    main()
