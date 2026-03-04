import os
import sys
import time
from langchain_classic.chains import RetrievalQA
from langchain_classic.prompts import PromptTemplate
import torch
from langchain_huggingface import HuggingFaceEmbeddings
from utils import get_chroma_vector_store, process_llm_response
from langchain_community.llms.ollama import Ollama
from langchain_groq import ChatGroq
from dotenv import load_dotenv


class Chat:
    def __init__(self, source, use_graph=None):
        self.source = source
        load_dotenv(override=True)

        # Determine if graph mode is enabled
        if use_graph is None:
            use_graph = os.getenv("USE_GRAPH", "false").lower() == "true"
            # Also check CLI args
            if "--use-graph" in sys.argv:
                use_graph = True
        self.use_graph = use_graph

        # --- LLM ---
        chat_model = os.getenv("CHAT_MODEL", "")
        if os.getenv("GROQ_API_KEY") and os.getenv("GROQ_API_KEY") != "":
            llm = ChatGroq(
                temperature=0,
                model=chat_model or "meta-llama/llama-4-scout-17b-16e-instruct",
            )
        else:
            # ollama qwen3.5:9b for local
            llm = Ollama(
                model=chat_model or "qwen3.5:9b",
                temperature=0,
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            )

        # --- Embedding model ---
        # Auto-detect device: use CUDA when available (local GPU), fall back to CPU (Docker / no GPU)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[EMBED] Using device: {device}")
        embed_model = HuggingFaceEmbeddings(
            model_name="BAAI/bge-m3",
            model_kwargs={"device": device},
            encode_kwargs={"normalize_embeddings": True},
        )

        # --- Vector store ---
        vector_store, chroma_client = get_chroma_vector_store(
            "confluence_docs", embed_model, "./chroma_db"
        )
        self._chroma_client = chroma_client

        # --- Retriever ---
        if self.use_graph:
            from graph_retriever import GraphRetriever

            retriever = GraphRetriever(
                vector_store=vector_store,
                vector_k=15,
                final_k=10,
                use_reranker=os.getenv("USE_RERANKER", "false").lower() == "true",
            )
            print("GraphRAG mode enabled (vector + graph retrieval)")
        else:
            retriever = vector_store.as_retriever(search_kwargs={"k": 5})
            print("Standard vector retrieval mode")

        # --- Prompt ---
        team_key = os.getenv("CONFLUENCE_TEAM_KEY")

        if self.use_graph:
            prompt_template = """You are a support agent for the {team}-team called Baymax. Use the following pieces of context from the {team}-team documents to give a detailed answer.

The context includes directly matching documents and related pages discovered via the knowledge graph. Pay attention to entity context and related pages for a more comprehensive answer.

If you don't know the answer, just say that you don't know, don't try to make up an answer.

Context:
{context}

Question: {question}
""".replace(
                "{team}", team_key or "TEAM"
            )
        else:
            prompt_template = """You are a support agent for the TEAM-team called Baymax, use the following pieces of context that comes from the TEAM-team documents and give a detailed anwser. If you don't know the answer, just say that you don't know, don't try to make up an answer.

        {context}

        Question: {question}
        """

        PROMPT = PromptTemplate(
            template=prompt_template, input_variables=["context", "question"]
        )
        chain_type_kwargs = {"prompt": PROMPT}
        self.qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=retriever,
            chain_type_kwargs=chain_type_kwargs,
            return_source_documents=True,
        )
        print("Chat initialized successfully")

    def close(self):
        """Release ChromaDB and other resources so files can be replaced."""
        if hasattr(self, '_chroma_client') and self._chroma_client is not None:
            try:
                self._chroma_client._client.close()
            except Exception:
                pass
            self._chroma_client = None

    def query(self, prompt):
        print(f"\n{'='*60}")
        print(f"[QUERY START] prompt: {prompt[:100]}...")
        t0 = time.time()

        print("[RETRIEVER] Calling qa_chain.invoke()...")
        try:
            result = self.qa_chain.invoke(prompt)
        except Exception as e:
            print(f"[CHAIN ERROR] {type(e).__name__}: {e}")
            raise
        t1 = time.time()
        print(f"[CHAIN DONE] took {t1-t0:.2f}s")
        print(f"   result keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")
        if isinstance(result, dict) and 'source_documents' in result:
            print(f"   source_documents count: {len(result['source_documents'])}")
        if isinstance(result, dict) and 'result' in result:
            print(f"   answer preview: {str(result['result'])[:200]}")

        print("[PROCESSING] Calling process_llm_response()...")
        try:
            response = process_llm_response(result)
        except Exception as e:
            print(f"[PROCESS ERROR] {type(e).__name__}: {e}")
            raise
        print(f"[DONE] Total query time: {time.time()-t0:.2f}s")
        print(f"{'='*60}\n")
        return response