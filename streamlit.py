import os
import tempfile

import streamlit as st

st.set_page_config(page_title="Baymax", page_icon="logo.webp", initial_sidebar_state="collapsed")

from chat import Chat
from data_manager import (
    export_data, import_data_staged, list_backups, finalize_chroma_swap,
)

# If a staged chroma_db import is pending, swap it in BEFORE Chat opens the DB.
finalize_chroma_swap()

@st.cache_resource
def get_chat():
    return Chat('streamlit')

chat = get_chat()

# ── Sidebar: Data Manager ──
with st.sidebar:
    st.header("Data Manager")
    st.caption("Export or import collected data (data/, chroma_db/, Neo4j)")

    # Export
    if st.button("Export Backup"):
        with st.spinner("Exporting data..."):
            try:
                zip_path = export_data()
                st.success(f"Exported: {zip_path.name}")
                with open(zip_path, "rb") as f:
                    st.download_button(
                        label="Download zip",
                        data=f,
                        file_name=zip_path.name,
                        mime="application/zip",
                    )
            except Exception as e:
                st.error(f"Export failed: {e}")

    # Import from uploaded file
    st.divider()
    uploaded = st.file_uploader("Import from zip", type=["zip"])
    if uploaded is not None:
        if st.button("Restore Backup"):
            with st.spinner("Importing data..."):
                try:
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
                    tmp.write(uploaded.read())
                    tmp.close()
                    import_data_staged(tmp.name)
                    os.remove(tmp.name)
                    st.success("Import complete!")
                    st.warning("Restart Streamlit (Ctrl+C → re-run) to load the new data.")
                except Exception as e:
                    st.error(f"Import failed: {e}")

    # Import from existing backups
    backups = list_backups()
    if backups:
        st.divider()
        st.subheader("Existing backups")
        chosen = st.selectbox("Select backup", backups, format_func=lambda p: p.name)
        if st.button("Restore Selected"):
            with st.spinner("Importing data..."):
                try:
                    import_data_staged(chosen)
                    st.success("Import complete!")
                    st.warning("Restart Streamlit (Ctrl+C → re-run) to load the new data.")
                except Exception as e:
                    st.error(f"Import failed: {e}")

col_logo, col_title = st.columns([0.08, 0.92])
with col_logo:
    st.image("logo.webp", width=42)
with col_title:
    st.title("Baymax")

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