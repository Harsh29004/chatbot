# The Python bot service: embeddings, retrieval, sheet readers, widget.
#
# This is the heavy image of the three — torch and the embedding model between
# them are most of it — which is exactly why it is now its own image. The Node
# API next to it rebuilds in seconds and does not drag a machine-learning stack
# along for the ride.

FROM python:3.12-slim

WORKDIR /app

# CPU-only torch on x86. The default wheel pulls the whole CUDA runtime —
# around 2 GB of it — for a box that has no GPU and never will. On arm64 there
# is no separate CPU index, so the default is already the right one.
RUN if [ "$(uname -m)" = "x86_64" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu; \
    else \
      pip install --no-cache-dir torch; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image.
#
# Without this the first container to start downloads ~90 MB from Hugging Face
# before it can answer anything — on every deploy, and on every restart of a
# container with no persistent cache. Worse, a deploy would then depend on
# huggingface.co being reachable, which is not a dependency a deploy should
# have. Pinned here so the layer only rebuilds when the model changes.
ARG EMBEDDING_MODEL=all-MiniLM-L6-v2
ENV EMBEDDING_MODEL=$EMBEDDING_MODEL
RUN python -c "from sentence_transformers import SentenceTransformer; \
               import os; SentenceTransformer(os.environ['EMBEDDING_MODEL'])"

# The package paths matter: `from backend.shared.config import ...` and
# `from bot.graph import ...` resolve exactly as they did in the original
# application, which is why those modules copied across unchanged.
COPY backend/ ./backend/
COPY bot/ ./bot/
COPY main.py .

# Loopback-only in compose. This service has no user-facing authentication
# because it has no users — the Node API resolves who is asking before
# forwarding anything here. Binding 0.0.0.0 inside the container is fine;
# compose publishes nothing for it.
EXPOSE 8001

# One worker. A second would load a second copy of the model into memory —
# and on the kind of box this is built for, that is the difference between
# comfortable and swapping. Scale with replicas, not workers.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
