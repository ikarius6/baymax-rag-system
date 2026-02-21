"""
graph_retriever.py - Hybrid vector + graph retriever for GraphRAG.

Combines ChromaDB vector search with Neo4j graph expansion and
optional BGE reranking for richer, context-aware retrieval.
"""

import os
from typing import List, Any
from dotenv import load_dotenv
from neo4j import GraphDatabase
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

load_dotenv(override=True)


class GraphRetriever(BaseRetriever):
    """
    Hybrid retriever that:
    1. Queries ChromaDB for top-k vector-similar documents.
    2. Expands results via Neo4j graph (parents, children, linked pages, shared entities).
    3. Optionally re-ranks with BGE reranker.
    4. Returns deduplicated, enriched documents.
    """

    vector_store: Any  # Chroma vector store
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "changeme")
    vector_k: int = 5
    graph_expansion_hops: int = 1
    final_k: int = 10
    use_reranker: bool = False
    alpha: float = 0.7  # weight for vector score vs graph proximity

    class Config:
        arbitrary_types_allowed = True

    def _get_neo4j_driver(self):
        return GraphDatabase.driver(self.neo4j_uri, auth=(self.neo4j_user, self.neo4j_password))

    def _get_relevant_documents(self, query: str, **kwargs) -> List[Document]:
        # Step 1: Vector search
        vector_results = self.vector_store.similarity_search_with_relevance_scores(query, k=self.vector_k)

        if not vector_results:
            return []

        # Collect page IDs and scores from vector results
        page_scores = {}
        page_docs = {}
        for doc, score in vector_results:
            page_id = doc.metadata.get("source", "")
            page_scores[page_id] = score
            page_docs[page_id] = doc

        # Step 2: Graph expansion
        driver = self._get_neo4j_driver()
        graph_docs = {}
        try:
            with driver.session() as session:
                for page_id in list(page_scores.keys()):
                    neighbors = self._expand_from_page(session, page_id)
                    for neighbor in neighbors:
                        nid = neighbor["page_id"]
                        if nid not in page_scores and nid not in graph_docs:
                            graph_docs[nid] = neighbor

                # Step 3: Community context
                top_page_id = max(page_scores, key=page_scores.get) if page_scores else None
                community_pages = []
                if top_page_id:
                    community_pages = self._get_community_context(session, top_page_id)

                # Get entity context for all vector results
                entity_context = self._get_entity_context(session, list(page_scores.keys()))
        finally:
            driver.close()

        # Step 4: Merge and build final documents
        all_docs = []

        # Add vector results first (highest priority)
        for page_id, doc in page_docs.items():
            doc.metadata["retrieval_type"] = "vector"
            doc.metadata["score"] = page_scores[page_id]
            if entity_context:
                doc.metadata["entity_context"] = entity_context
            all_docs.append(doc)

        # Add graph-expanded documents
        seen_ids = set(page_docs.keys())
        for nid, neighbor in graph_docs.items():
            if nid not in seen_ids:
                doc = Document(
                    page_content=neighbor.get("content_preview", ""),
                    metadata={
                        "source": neighbor.get("tiny_link", ""),
                        "title": neighbor.get("title", ""),
                        "retrieval_type": "graph_expansion",
                        "relationship": neighbor.get("relationship", "related"),
                    },
                )
                all_docs.append(doc)
                seen_ids.add(nid)

        # Add community pages
        for cp in community_pages:
            cpid = cp["page_id"]
            if cpid not in seen_ids:
                doc = Document(
                    page_content=cp.get("content_preview", ""),
                    metadata={
                        "source": cp.get("tiny_link", ""),
                        "title": cp.get("title", ""),
                        "retrieval_type": "community",
                    },
                )
                all_docs.append(doc)
                seen_ids.add(cpid)

        # Step 5: Optional reranking
        if self.use_reranker and len(all_docs) > 1:
            all_docs = self._rerank(query, all_docs)

        return all_docs[: self.final_k]

    def _expand_from_page(self, session, page_id: str) -> list:
        """Get 1-hop neighbors: parents, children, linked pages, pages sharing entities."""
        result = session.run(
            """
            MATCH (p:Page)
            WHERE p.page_id = $pid OR p.tiny_link = $pid
            OPTIONAL MATCH (p)-[:CHILD_OF]->(parent:Page)
            OPTIONAL MATCH (child:Page)-[:CHILD_OF]->(p)
            OPTIONAL MATCH (p)-[:LINKS_TO]->(linked:Page)
            OPTIONAL MATCH (linked_back:Page)-[:LINKS_TO]->(p)
            OPTIONAL MATCH (e:Entity)-[:MENTIONED_IN]->(p)
            OPTIONAL MATCH (e)-[:MENTIONED_IN]->(shared:Page)
            WHERE shared.page_id <> p.page_id
            WITH [x IN collect(DISTINCT parent) + collect(DISTINCT child) +
                 collect(DISTINCT linked) + collect(DISTINCT linked_back) +
                 collect(DISTINCT shared) WHERE x IS NOT NULL] AS neighbors
            UNWIND neighbors AS n
            RETURN DISTINCT n.page_id AS page_id, n.title AS title,
                   n.tiny_link AS tiny_link, n.content_preview AS content_preview
            LIMIT 20
            """,
            pid=str(page_id),
        )
        return [dict(record) for record in result]

    def _get_community_context(self, session, page_id: str) -> list:
        """Get pages in the same community as the given page."""
        result = session.run(
            """
            MATCH (p:Page)
            WHERE p.page_id = $pid OR p.tiny_link = $pid
            WITH p.community_id AS cid
            WHERE cid IS NOT NULL
            MATCH (q:Page {community_id: cid})
            WHERE q.page_id <> $pid
            RETURN q.page_id AS page_id, q.title AS title,
                   q.tiny_link AS tiny_link, q.content_preview AS content_preview
            LIMIT 5
            """,
            pid=str(page_id),
        )
        return [dict(record) for record in result]

    def _get_entity_context(self, session, page_ids: list) -> str:
        """Get entities mentioned in the given pages for additional context."""
        result = session.run(
            """
            MATCH (e:Entity)-[:MENTIONED_IN]->(p:Page)
            WHERE p.page_id IN $pids OR p.tiny_link IN $pids
            RETURN DISTINCT e.name AS name, e.type AS type
            ORDER BY e.type, e.name
            LIMIT 30
            """,
            pids=[str(pid) for pid in page_ids],
        )
        entities = [f"{r['name']} ({r['type']})" for r in result]
        return ", ".join(entities) if entities else ""

    def _rerank(self, query: str, docs: List[Document]) -> List[Document]:
        """Re-rank documents using a cross-encoder reranker."""
        try:
            from sentence_transformers import CrossEncoder

            reranker = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cuda" if __import__("torch").cuda.is_available() else "cpu")
            pairs = [(query, doc.page_content) for doc in docs]
            scores = reranker.predict(pairs)

            scored_docs = list(zip(docs, scores))
            scored_docs.sort(key=lambda x: x[1], reverse=True)
            return [doc for doc, _ in scored_docs]
        except Exception as e:
            print(f"Reranking failed, returning original order: {e}")
            return docs
