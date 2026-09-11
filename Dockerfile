# ---------------------------------------------------------------------------
# Stage 1 — build the frontend
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /frontend

# Copy manifests first so `npm ci` is cached until dependencies actually change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 — the backend, serving the built frontend
# ---------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# CPU-only PyTorch first — saves ~1.5GB against the default CUDA build.
#
# The pytorch.org CPU index carries x86_64 wheels; on aarch64 (an Oracle
# Ampere box, an Apple-silicon laptop) it has nothing to offer and pip would
# fall through to building from source. PyPI's own linux_aarch64 wheels are
# already CPU-only, so ARM just uses those.
RUN if [ "$(uname -m)" = "x86_64" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu; \
    else \
      pip install --no-cache-dir torch; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image. Downloading it at startup would put
# a ~15s stall in front of the first request of every cold container.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY backend/ ./backend/
COPY bot/ ./bot/
COPY benchmark.py ./

# The built SPA, served by the backend so there is one origin and the session
# cookie needs no cross-site handling.
COPY --from=frontend /frontend/dist ./frontend/dist

EXPOSE 8000

# Fail the container, not just the request, if the app stops answering.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
