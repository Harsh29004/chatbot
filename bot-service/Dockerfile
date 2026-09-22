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

# How many worker processes, and how many torch threads each one gets.
#
# This is the only knob that matters for throughput, because embedding is the
# only slow thing in the stack — Node answers 2,400+ req/s, MongoDB 960, this
# service around 110. Measured, one short query each, on a 20-core host:
#
#     1 worker  x 2 threads    80 req/s    12 ms
#     2 workers x 2 threads   132 req/s    15 ms
#     4 workers x 2 threads   155 req/s    26 ms
#     4 workers x 1 thread    170 req/s    20 ms
#     6 workers x 1 thread    187 req/s    23 ms
#
# Two things worth reading off that. It scales, but sublinearly — six workers
# buy 2.3x, not 6x, because the work is already memory-bandwidth bound. And
# past one worker, *one* thread each beats two: the workers stop competing for
# the same cores.
#
# So: one worker while a single request should be as fast as possible, more
# workers when many arrive at once. Each one holds its own copy of the model,
# roughly 400 MB with torch, which is what caps this on a small box.
#
# Defaults suit a 2-4 core instance. Shell form so the variables expand.
ENV BOT_WORKERS=1 \
    TORCH_THREADS=2

CMD uvicorn main:app --host 0.0.0.0 --port 8001 --workers ${BOT_WORKERS}
