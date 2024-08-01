import pandas as pd
from utils import get_chroma_vector_store, import_csv, clean_data_schema, generate_embeddings_and_add_to_df, upsert_data
from dotenv import load_dotenv

from langchain_community.embeddings.huggingface import HuggingFaceBgeEmbeddings
from langchain.globals import set_verbose

set_verbose(True)
load_dotenv(override=True)

embed_model = HuggingFaceBgeEmbeddings(
    model_name='BAAI/bge-m3',
    model_kwargs={'device': 'cuda'},
    encode_kwargs={'normalize_embeddings': True}
)

vector_store, chroma_client = get_chroma_vector_store('confluence_docs', embed_model, './chroma_db')

df = pd.DataFrame(columns=['id', 'tiny_link', 'content'])
df = import_csv(df, './data/kb.csv', max_rows=2000) 
df = clean_data_schema(df)
df = generate_embeddings_and_add_to_df(df, embed_model)
collection = upsert_data(df, 'confluence_docs', chroma_client)

print('Indexes created')
