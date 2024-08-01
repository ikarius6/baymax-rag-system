from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from langchain_community.embeddings.huggingface import HuggingFaceBgeEmbeddings
from utils import get_chroma_vector_store, process_llm_response
from langchain_community.llms.ollama import Ollama
from langchain_groq import ChatGroq
from dotenv import load_dotenv
import os

class Chat:
    def __init__(self, source):
        self.source = source
        load_dotenv(override=True)

        # if GROQ_API_KEY exist use GROQ, else use local Llama3
        if os.getenv("GROQ_API_KEY") and os.getenv("GROQ_API_KEY") != "":
            llm = ChatGroq(
                temperature=0,
                # check limits https://console.groq.com/settings/limits
                model="llama-3.1-8b-instant",
            )
        else:
            llm = Ollama(
                model="llama3", 
                temperature=0,
                base_url='http://localhost:11434'
            )

        embed_model = HuggingFaceBgeEmbeddings(
            model_name='BAAI/bge-m3',
            model_kwargs={'device': 'cuda'},
            encode_kwargs={'normalize_embeddings': True}
        )

        vector_store, chroma_client = get_chroma_vector_store('confluence_docs', embed_model, './chroma_db')
        retriever = vector_store.as_retriever(search_kwargs={"k": 5})
        team_key = os.getenv("CONFLUENCE_TEAM_KEY")
        prompt_template = """You are a support agent for the TEAM-team called Baymax, use the following pieces of context that comes from the TEAM-team documents and give a detailed anwser. If you don't know the answer, just say that you don't know, don't try to make up an answer.

        {context}

        Question: {question}
        """
        PROMPT = PromptTemplate(
            template=prompt_template, input_variables=["context", "question"]
        )
        chain_type_kwargs = {"prompt": PROMPT}
        self.qa_chain = RetrievalQA.from_chain_type(llm=llm,
                                        chain_type="stuff",
                                        retriever=retriever,
                                        chain_type_kwargs=chain_type_kwargs,
                                        return_source_documents=True)
    
    def query(self, prompt):
        result = self.qa_chain.invoke(prompt)
        return process_llm_response(result)