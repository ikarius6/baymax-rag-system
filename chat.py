import os
import sys
from langchain_classic.chains import RetrievalQA
from langchain_classic.prompts import PromptTemplate
from langchain_community.embeddings.huggingface import HuggingFaceBgeEmbeddings
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
        if os.getenv("GROQ_API_KEY") and os.getenv("GROQ_API_KEY") != "":
            llm = ChatGroq(
                temperature=0,
                model="llama-3.1-8b-instant",
            )
        else:
            llm = Ollama(
                model="llama3",
                temperature=0,
                base_url="http://localhost:11434",
            )

        # --- Embedding model ---
        embed_model = HuggingFaceBgeEmbeddings(
            model_name="BAAI/bge-m3",
            model_kwargs={"device": "cuda"},
            encode_kwargs={"normalize_embeddings": True},
        )

        # --- Vector store ---
        vector_store, chroma_client = get_chroma_vector_store(
            "confluence_docs", embed_model, "./chroma_db"
        )

        # --- Retriever ---
        if self.use_graph:
            from graph_retriever import GraphRetriever

            retriever = GraphRetriever(
                vector_store=vector_store,
                vector_k=5,
                final_k=10,
                use_reranker=os.getenv("USE_RERANKER", "false").lower() == "true",
            )
            print("🔗 GraphRAG mode enabled (vector + graph retrieval)")
        else:
            retriever = vector_store.as_retriever(search_kwargs={"k": 5})
            print("📄 Standard vector retrieval mode")

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

    def query(self, prompt):
        result = self.qa_chain.invoke(prompt)
        return process_llm_response(result)