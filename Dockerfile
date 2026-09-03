# ---------------------------------------------------------------------------
# Stage 1 — build the web app
# ---------------------------------------------------------------------------
FROM node:20-slim AS web

WORKDIR /web

# Copy manifests first so `npm ci` is cached until dependencies actually change.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 — the API, serving the built web app
# ---------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# CPU-only PyTorch first — saves ~1.5GB against the default CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image. Downloading it at startup would put
# a ~15s stall in front of the first request of every cold container.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY apps/ ./apps/
COPY shared/ ./shared/
COPY scripts/ ./scripts/
COPY server.py benchmark.py ./

# The built SPA, served by the API so there is one origin and the session
# cookie needs no cross-site handling.
COPY --from=web /web/dist ./web/dist

EXPOSE 8000

# Fail the container, not just the request, if the app stops answering.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
