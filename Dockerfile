# ── Baymax RAG System ──────────────────────────────────────────────────────────
# Streamlit chat interface + Python dependencies (CPU-only torch)
# For GPU inference, run without Docker and install the CUDA torch build manually.
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# System deps for lxml / chromadb native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install Python dependencies ───────────────────────────────────────────────
# Copy requirements first so this layer is cached unless requirements change.
COPY requirements.txt .

# Install CPU-only PyTorch first (much smaller than the CUDA build)
RUN pip install --no-cache-dir \
        torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cpu

# Install the rest of the packages
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy application code ──────────────────────────────────────────────────────
COPY . .

# ── Runtime ───────────────────────────────────────────────────────────────────
EXPOSE 8501

# Streamlit must listen on 0.0.0.0 to be reachable from outside the container
CMD ["streamlit", "run", "streamlit.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]
