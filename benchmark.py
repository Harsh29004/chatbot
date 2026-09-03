"""
Performance benchmark for the Nexora AI answer pipeline.

Measures each stage and the whole thing end to end:
  1. Guardrails (regex)
  2. Embedding (sentence-transformers, CPU)
  3. ChromaDB retrieval
  4. Score routing + response

Runs against a representative shop FAQ indexed into a throwaway collection,
using the same engine and the same e-commerce template a real customer's bot
runs on. Numbers quoted in the README and on the landing page come from here.

Usage:
    python benchmark.py
"""

from __future__ import annotations

import csv
import io
import statistics
import sys
import time
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BENCH_COLLECTION = "benchmark_index"
TEMPLATE_ID = "ecommerce"


# ── The sheet under test ─────────────────────────────────────────────────
# Representative of what a small shop actually uploads.

SHEET_ROWS: list[list[str]] = [
    ["Question", "Alt_Phrasings", "Category", "Answer"],
    ["How do I cancel an order?", "order cancellation; call off an order", "Orders",
     "Open My Orders, select the order and choose Cancel Order. Cancellation is free before dispatch."],
    ["How do I get a refund?", "money back; refund timeline", "Refunds",
     "Refunds are processed within 5 to 7 working days once the returned item reaches our warehouse."],
    ["How do I track my order?", "order tracking; delivery status", "Orders",
     "Open My Orders and tap the order to see live tracking."],
    ["What are your delivery charges?", "shipping cost; delivery fee", "Shipping",
     "Delivery is free on orders above 499. Below that, a flat fee of 49 applies."],
    ["How do I return an item?", "start a return; return policy", "Returns",
     "Go to My Orders, select the item and tap Return, within 7 days of delivery."],
    ["What payment methods do you accept?", "payment options; accepted cards", "Payments",
     "We accept UPI, credit and debit cards, net banking, and cash on delivery."],
    ["How do I change my phone number?", "update mobile; edit contact", "Account",
     "Open Settings and tap Edit Profile. You will receive an OTP to confirm."],
    ["Do you deliver to my city?", "serviceable pin codes; delivery area", "Shipping",
     "We deliver nationwide. Enter your pin code at checkout to see the delivery estimate."],
    ["Can I change my delivery address?", "wrong address; update address", "Orders",
     "The address can be changed from My Orders any time before the order is dispatched."],
    ["Do you offer cash on delivery?", "cod; pay on delivery", "Payments",
     "Cash on delivery is available on orders up to 5000 in serviceable pin codes."],
]

QUERIES = {
    "strong_match": [
        "How do I cancel an order?",
        "How do I get a refund?",
        "What payment methods do you accept?",
        "What are your delivery charges?",
        "How do I return an item?",
    ],
    "near_match": [
        "where is my money back",
        "what are the payment options available",
        "how much do you charge for shipping",
        "I want to send an item back",
        "can I update my mobile number",
    ],
    "out_of_scope": [
        "What's the weather today?",
        "Tell me a joke",
        "Who is the president?",
        "Can you write Python code?",
        "Explain quantum mechanics",
    ],
}


# ── Step-level timing wrappers ───────────────────────────────────────────

def time_guardrails(query: str) -> tuple[dict, float]:
    from shared.guardrails import detect_action_intent, detect_injection

    start = time.perf_counter()
    injection = detect_injection(query)
    action = detect_action_intent(query)
    elapsed = time.perf_counter() - start
    return {"flagged_injection": injection, "is_action_request": action}, elapsed


def time_embedding(query: str) -> tuple[list[float], float]:
    from shared.embeddings import embed_text

    start = time.perf_counter()
    embedding = embed_text(query)
    elapsed = time.perf_counter() - start
    return embedding, elapsed


def time_retrieval(collection_name: str, embedding: list[float], k: int = 3):
    from shared.vector_store import get_collection, query_collection

    collection = get_collection(collection_name)
    start = time.perf_counter()
    results = query_collection(collection, embedding, k=k)
    elapsed = time.perf_counter() - start
    return results, elapsed


def time_full_graph(graph, query: str, session_id: str = "bench") -> tuple[dict, float]:
    start = time.perf_counter()
    result = graph.invoke({"query": query, "session_id": session_id})
    elapsed = time.perf_counter() - start
    return result, elapsed


# ── Formatting helpers ───────────────────────────────────────────────────

def ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f}ms"


def calc_stats(times: list[float]) -> dict[str, float]:
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
    s = calc_stats(times)
    print(f"  {label:<22} avg={ms(s['avg']):>8}  min={ms(s['min']):>8}  "
          f"max={ms(s['max']):>8}  p50={ms(s['p50']):>8}  p95={ms(s['p95']):>8}")


def print_header(title: str) -> None:
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")


# ── Main benchmark ───────────────────────────────────────────────────────

def run_benchmark() -> None:
    print_header("NEXORA AI — ANSWER PIPELINE BENCHMARK")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    print("\n  Initializing...")

    from shared.logging_store import init_db
    init_db()

    from apps.bot_engine.graph import BotConfig, build_graph
    from apps.bot_engine.ingest import ingest_sheet
    from apps.bot_engine.templates import get_template

    template = get_template(TEMPLATE_ID)

    buffer = io.StringIO()
    csv.writer(buffer).writerows(SHEET_ROWS)
    sheet_bytes = buffer.getvalue().encode()

    # Warm up the embedding model before timing anything — the first call
    # loads it from disk and would otherwise be charged to ingestion.
    print("  Warming up embedding model...")
    t0 = time.perf_counter()
    from shared.embeddings import embed_text
    embed_text("warmup query")
    model_load_time = time.perf_counter() - t0
    print(f"  Model loaded in {ms(model_load_time)}")

    t0 = time.perf_counter()
    result = ingest_sheet(
        collection_name=BENCH_COLLECTION, filename="bench.csv", data=sheet_bytes
    )
    ingest_time = time.perf_counter() - t0
    print(f"  Sheet: {result.documents_indexed} entries "
          f"({result.vectors_indexed} phrasings) indexed in {ms(ingest_time)}")

    graph = build_graph(
        BotConfig(
            collection_name=BENCH_COLLECTION,
            decline_message=template.decline_message,
            near_match_suffix=template.near_match_suffix,
            strong_threshold=template.strong_threshold,
            near_threshold=template.near_threshold,
            log_label="benchmark",
            extra_action_patterns=template.extra_action_patterns,
        )
    )
    print(f"  Template: {template.name} "
          f"(strong >= {template.strong_threshold}, near >= {template.near_threshold})")

    # ── Step-level ───────────────────────────────────────────────────────
    print_header("STEP-LEVEL TIMING (per component)")

    all_queries: list[str] = []
    for queries in QUERIES.values():
        all_queries.extend(queries)

    guard_times = []
    for q in all_queries:
        _, t = time_guardrails(q)
        guard_times.append(t)
    print_stats("Guardrails", guard_times)

    embed_times, embeddings = [], []
    for q in all_queries:
        emb, t = time_embedding(q)
        embed_times.append(t)
        embeddings.append(emb)
    print_stats("Embedding", embed_times)

    retrieval_times = []
    for emb in embeddings:
        _, t = time_retrieval(BENCH_COLLECTION, emb)
        retrieval_times.append(t)
    print_stats("ChromaDB Retrieval", retrieval_times)

    # ── End to end ───────────────────────────────────────────────────────
    print_header("END-TO-END TIMING (full pipeline)")

    all_e2e_times: list[float] = []
    for query_type, queries in QUERIES.items():
        times, modes = [], []
        for q in queries:
            res, t = time_full_graph(graph, q)
            times.append(t)
            modes.append(res.get("mode", "?"))
            all_e2e_times.append(t)

        mode_counts: dict[str, int] = {}
        for m in modes:
            mode_counts[m] = mode_counts.get(m, 0) + 1
        mode_str = ", ".join(f"{k}={v}" for k, v in mode_counts.items())

        print_stats(query_type, times)
        print(f"  {'':>22} modes: {mode_str}")

    print_stats("ALL", all_e2e_times)

    # ── Throughput ───────────────────────────────────────────────────────
    print_header("THROUGHPUT (50 sequential requests)")

    throughput_queries = QUERIES["strong_match"] * 10
    t0 = time.perf_counter()
    for q in throughput_queries:
        graph.invoke({"query": q, "session_id": "throughput"})
    total_time = time.perf_counter() - t0

    qps = len(throughput_queries) / total_time
    avg_latency = total_time / len(throughput_queries)

    print(f"  Queries:         {len(throughput_queries)}")
    print(f"  Total time:      {ms(total_time)}")
    print(f"  Avg latency:     {ms(avg_latency)}")
    print(f"  Throughput:      {qps:.1f} queries/sec")

    # ── Summary ──────────────────────────────────────────────────────────
    print_header("SUMMARY")
    print("  Embedding model:       all-MiniLM-L6-v2 (CPU)")
    print(f"  Model load time:       {ms(model_load_time)}")
    print(f"  Avg guardrails:        {ms(statistics.mean(guard_times))}")
    print(f"  Avg embedding:         {ms(statistics.mean(embed_times))}")
    print(f"  Avg retrieval:         {ms(statistics.mean(retrieval_times))}")
    print(f"  Avg end-to-end:        {ms(statistics.mean(all_e2e_times))}")
    print(f"  Throughput:            {qps:.1f} queries/sec")
    print()


if __name__ == "__main__":
    run_benchmark()
