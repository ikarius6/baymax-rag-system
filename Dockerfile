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

# TORCH_VARIANT controls which PyTorch build is installed:
#   cpu   (default) — works everywhere, no GPU required
#   cu128           — CUDA 12.8, requires an NVIDIA GPU + nvidia-container-toolkit on the host
ARG TORCH_VARIANT=cpu
RUN if [ "$TORCH_VARIANT" = "cpu" ]; then \
        pip install --no-cache-dir \
            torch torchvision torchaudio \
            --index-url https://download.pytorch.org/whl/cpu; \
    else \
        pip install --no-cache-dir \
            --pre torch torchvision torchaudio \
            --index-url https://download.pytorch.org/whl/nightly/${TORCH_VARIANT}; \
    fi

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
