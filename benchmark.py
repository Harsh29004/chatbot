"""
Performance Benchmark for FAQ Bot Pipeline.

Measures response times across the full pipeline:
  1. Guardrails check
  2. Embedding generation (sentence-transformers)
  3. ChromaDB vector retrieval
  4. Score routing + response

Reports per-step and end-to-end timing with stats
(avg, min, max, p50, p95) for both bots.

Usage:
    python benchmark.py
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Step-level timing wrappers ───────────────────────────────────────────

def time_guardrails(query: str) -> tuple[dict, float]:
    """Time the guardrails check."""
    from shared.guardrails import detect_action_intent, detect_injection

    start = time.perf_counter()
    injection = detect_injection(query)
    action = detect_action_intent(query)
    elapsed = time.perf_counter() - start
    return {"flagged_injection": injection, "is_action_request": action}, elapsed


def time_embedding(query: str) -> tuple[list[float], float]:
    """Time the embedding generation."""
    from shared.embeddings import embed_text

    start = time.perf_counter()
    embedding = embed_text(query)
    elapsed = time.perf_counter() - start
    return embedding, elapsed


def time_retrieval(collection_name: str, embedding: list[float], k: int = 3) -> tuple[list[dict], float]:
    """Time the ChromaDB vector search."""
    from shared.vector_store import get_collection, query_collection

    collection = get_collection(collection_name)
    start = time.perf_counter()
    results = query_collection(collection, embedding, k=k)
    elapsed = time.perf_counter() - start
    return results, elapsed


def time_full_graph(graph, query: str, session_id: str = "bench") -> tuple[dict, float]:
    """Time the full LangGraph invocation."""
    start = time.perf_counter()
    result = graph.invoke({"query": query, "session_id": session_id})
    elapsed = time.perf_counter() - start
    return result, elapsed


# ── Formatting helpers ───────────────────────────────────────────────────

def ms(seconds: float) -> str:
    """Format seconds as milliseconds with 1 decimal."""
    return f"{seconds * 1000:.1f}ms"


def calc_stats(times: list[float]) -> dict[str, float]:
    """Calculate timing statistics."""
    sorted_t = sorted(times)
    n = len(sorted_t)
    return {
        "avg": statistics.mean(sorted_t),
        "min": min(sorted_t),
        "max": max(sorted_t),
        "p50": sorted_t[n // 2],
        "p95": sorted_t[int(n * 0.95)] if n >= 20 else sorted_t[-1],
    }


def print_stats(label: str, times: list[float]) -> None:
    """Print a row of timing stats."""
    s = calc_stats(times)
    print(f"  {label:<22} avg={ms(s['avg']):>8}  min={ms(s['min']):>8}  "
          f"max={ms(s['max']):>8}  p50={ms(s['p50']):>8}  p95={ms(s['p95']):>8}")


def print_header(title: str) -> None:
    """Print a section header."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")


# ── Test queries ─────────────────────────────────────────────────────────

CUSTOMER_QUERIES = {
    "strong_match": [
        "How do I cancel a booking?",
        "How do I get a refund?",
        "What payment methods do you accept?",
        "How do I reschedule a booking?",
        "How do I change my phone number?",
    ],
    "near_match": [
        "Can I cancel my service appointment?",
        "Where is my money back?",
        "What are the payment options available?",
        "I want to move my appointment to another day",
        "How to update my mobile number?",
    ],
    "out_of_scope": [
        "What's the weather today?",
        "Tell me a joke",
        "Who is the president?",
        "Can you write Python code?",
        "Explain quantum mechanics",
    ],
}

PARTNER_QUERIES = {
    "strong_match": [
        "How do I check my KYC status?",
        "When do I receive my payout?",
        "How are jobs assigned to me?",
        "How is my rating calculated?",
        "What happens if I cancel a job after accepting?",
    ],
    "near_match": [
        "What is my KYC verification status?",
        "When will I get paid?",
        "How does job assignment work?",
        "How do customer reviews affect my rating?",
        "What if I cancel an accepted job?",
    ],
    "out_of_scope": [
        "What's the weather today?",
        "Tell me a joke",
        "Who is the president?",
        "Can you write Python code?",
        "Explain quantum mechanics",
    ],
}


# ── Main benchmark ───────────────────────────────────────────────────────

def run_benchmark():
    print_header("FAQ BOT PERFORMANCE BENCHMARK")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # ── 1. Initialize ────────────────────────────────────────────────────
    print("\n  Initializing...")

    # Init DB + ingest data
    from shared.logging_store import init_db
    from shared.api_keys import init_api_key_tables
    init_db()
    init_api_key_tables()

    from apps.customer_bot.ingest import ingest as customer_ingest
    from apps.partner_bot.ingest import ingest as partner_ingest

    t0 = time.perf_counter()
    c_count = customer_ingest()
    customer_ingest_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    p_count = partner_ingest()
    partner_ingest_time = time.perf_counter() - t0

    print(f"  Customer FAQ: {c_count} docs ingested in {ms(customer_ingest_time)}")
    print(f"  Partner FAQ:  {p_count} docs ingested in {ms(partner_ingest_time)}")

    # Warm up the embedding model (first call loads the model)
    print("  Warming up embedding model...")
    t0 = time.perf_counter()
    from shared.embeddings import embed_text
    embed_text("warmup query")
    model_load_time = time.perf_counter() - t0
    print(f"  Model loaded in {ms(model_load_time)}")

    # Build graphs
    from apps.customer_bot.graph import build_customer_graph
    from apps.partner_bot.graph import build_partner_graph
    customer_graph = build_customer_graph()
    partner_graph = build_partner_graph()

    # ── 2. Step-level benchmarks ─────────────────────────────────────────
    print_header("STEP-LEVEL TIMING (per component)")

    all_queries = []
    for queries in CUSTOMER_QUERIES.values():
        all_queries.extend(queries)

    # Guardrails
    guard_times = []
    for q in all_queries:
        _, t = time_guardrails(q)
        guard_times.append(t)
    print_stats("Guardrails", guard_times)

    # Embedding
    embed_times = []
    embeddings = []
    for q in all_queries:
        emb, t = time_embedding(q)
        embed_times.append(t)
        embeddings.append(emb)
    print_stats("Embedding", embed_times)

    # Retrieval
    retrieval_times = []
    from shared.config import CUSTOMER_COLLECTION
    for emb in embeddings:
        _, t = time_retrieval(CUSTOMER_COLLECTION, emb)
        retrieval_times.append(t)
    print_stats("ChromaDB Retrieval", retrieval_times)

    # ── 3. End-to-end graph benchmarks ───────────────────────────────────
    print_header("END-TO-END GRAPH TIMING (full pipeline)")

    for bot_name, graph, queries_dict in [
        ("Customer Bot", customer_graph, CUSTOMER_QUERIES),
        ("Partner Bot", partner_graph, PARTNER_QUERIES),
    ]:
        print(f"\n  -- {bot_name} --")
        all_e2e_times = []

        for query_type, queries in queries_dict.items():
            times = []
            modes = []
            for q in queries:
                result, t = time_full_graph(graph, q)
                times.append(t)
                modes.append(result.get("mode", "?"))
                all_e2e_times.append(t)

            mode_counts = {}
            for m in modes:
                mode_counts[m] = mode_counts.get(m, 0) + 1
            mode_str = ", ".join(f"{k}={v}" for k, v in mode_counts.items())

            print_stats(f"{query_type}", times)
            print(f"  {'':>22} modes: {mode_str}")

        print_stats(f"ALL ({bot_name})", all_e2e_times)

    # ── 4. Throughput test ───────────────────────────────────────────────
    print_header("THROUGHPUT TEST (50 sequential requests)")

    throughput_queries = CUSTOMER_QUERIES["strong_match"] * 10  # 50 queries
    t0 = time.perf_counter()
    for q in throughput_queries:
        customer_graph.invoke({"query": q, "session_id": "throughput"})
    total_time = time.perf_counter() - t0

    qps = len(throughput_queries) / total_time
    avg_latency = total_time / len(throughput_queries)

    print(f"  Queries:         {len(throughput_queries)}")
    print(f"  Total time:      {ms(total_time)}")
    print(f"  Avg latency:     {ms(avg_latency)}")
    print(f"  Throughput:      {qps:.1f} queries/sec")

    # ── 5. Summary ───────────────────────────────────────────────────────
    print_header("SUMMARY")
    print(f"  Embedding model:       all-MiniLM-L6-v2 (CPU)")
    print(f"  Model load time:       {ms(model_load_time)}")
    print(f"  Avg guardrails:        {ms(statistics.mean(guard_times))}")
    print(f"  Avg embedding:         {ms(statistics.mean(embed_times))}")
    print(f"  Avg retrieval:         {ms(statistics.mean(retrieval_times))}")
    print(f"  Avg end-to-end:        {ms(avg_latency)}")
    print(f"  Throughput:            {qps:.1f} queries/sec")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    run_benchmark()
