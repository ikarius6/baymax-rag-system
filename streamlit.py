import streamlit as st
from chat import Chat

chat = Chat('streamlit')

st.title("💬 Baymax")

if "messages" not in st.session_state:
    st.session_state["messages"] = [{"role": "assistant", "content": "Hey I'm Baymax. How can I help you?"}]

for msg in st.session_state.messages:
    st.chat_message(msg["role"]).write(msg["content"])

if prompt := st.chat_input():
    st.session_state.messages.append({"role": "user", "content": prompt})  
    st.chat_message("user").write(prompt)

    msg = chat.query(prompt)

    st.session_state.messages.append({"role": "assistant", "content": msg})
    st.chat_message("assistant").write(msg)