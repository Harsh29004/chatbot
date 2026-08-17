FROM python:3.12-slim

WORKDIR /app

# Install CPU-only PyTorch first (saves ~1.5GB vs full CUDA version)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model at build time (not at startup)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy source code
COPY . .

# Generate dummy data if Excel files don't exist
RUN python generate_dummy_data.py || true

# Expose the API port
EXPOSE 8000

# Run the server
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

