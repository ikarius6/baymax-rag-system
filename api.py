"""
api.py – FastAPI server that exposes Baymax RAG chat for a VS Code extension.

Start with:
    uvicorn api:app --host 127.0.0.1 --port 8888 --reload

Endpoints
---------
POST /chat          – send a message and get an answer
GET  /history       – retrieve conversation history for a session
DELETE /history     – clear conversation history for a session
GET  /health        – liveness check
"""

import os
import uuid
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from data_manager import finalize_chroma_swap
from chat import Chat

# ---------------------------------------------------------------------------
# Boot-time setup
# ---------------------------------------------------------------------------
load_dotenv(override=True)
finalize_chroma_swap()          # same guard as streamlit.py

app = FastAPI(
    title="Baymax RAG API",
    description="REST interface for the Baymax RAG chat system. Designed for consumption by the VS Code extension.",
    version="1.0.0",
)

# Allow VS Code webview / localhost callers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Singleton Chat instance (shared across all sessions, thread-safe for reads)
# ---------------------------------------------------------------------------
_chat: Optional[Chat] = None

def get_chat() -> Chat:
    global _chat
    if _chat is None:
        _chat = Chat("api")
    return _chat


# ---------------------------------------------------------------------------
# In-memory session history  { session_id -> [ {role, content}, … ] }
# ---------------------------------------------------------------------------
_history: dict[str, list[dict]] = defaultdict(list)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None   # omit → auto-generated fresh session


class MessageRecord(BaseModel):
    role: str       # "user" | "assistant"
    content: str


class ChatResponse(BaseModel):
    session_id: str
    message: str
    history: list[MessageRecord]


class HistoryResponse(BaseModel):
    session_id: str
    history: list[MessageRecord]


class HealthResponse(BaseModel):
    status: str
    chat_model: str
    use_graph: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["Utility"])
def health():
    """Quick liveness / config check."""
    chat = get_chat()
    return HealthResponse(
        status="ok",
        chat_model=os.getenv("CHAT_MODEL", "default"),
        use_graph=chat.use_graph,
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
def chat_endpoint(req: ChatRequest):
    """
    Send a message to Baymax and receive an answer.

    - **message**: the user prompt
    - **session_id**: optional; supply the value returned by a previous call
      to maintain conversation context in the history log.
      If omitted a new session UUID is created.
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    session_id = req.session_id or str(uuid.uuid4())
    chat = get_chat()

    # Persist user message
    _history[session_id].append({"role": "user", "content": req.message})

    try:
        answer = chat.query(req.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat error: {exc}") from exc

    # Persist assistant message
    _history[session_id].append({"role": "assistant", "content": answer})

    return ChatResponse(
        session_id=session_id,
        message=answer,
        history=[MessageRecord(**m) for m in _history[session_id]],
    )


@app.get("/history/{session_id}", response_model=HistoryResponse, tags=["Chat"])
def get_history(session_id: str):
    """Return the full conversation history for a session."""
    if session_id not in _history:
        raise HTTPException(status_code=404, detail="Session not found")
    return HistoryResponse(
        session_id=session_id,
        history=[MessageRecord(**m) for m in _history[session_id]],
    )


@app.delete("/history/{session_id}", tags=["Chat"])
def clear_history(session_id: str):
    """Clear conversation history for a session."""
    _history.pop(session_id, None)
    return {"detail": f"History for session {session_id!r} cleared."}
