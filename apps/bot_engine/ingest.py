"""
Turn a customer's uploaded sheet into a searchable index.

Accepts .xlsx or .csv. Only ``Question`` and ``Answer`` are required —
``Alt_Phrasings`` and ``Category`` improve matching when present but asking
for four mandatory columns turns a five-minute setup into an afternoon.

The searchable text is question + phrasings + category; the *answer* is
carried as metadata and returned verbatim. Nothing generates prose, so the
answer a user sees is exactly the answer the customer wrote.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pandas as pd

from shared.embeddings import embed_batch
from shared.vector_store import add_documents, reset_collection

from apps.bot_engine.templates import OPTIONAL_SHEET_COLUMNS, REQUIRED_SHEET_COLUMNS

# A sheet this size already covers a very large support surface; beyond it,
# someone is uploading a database export by mistake.
MAX_ROWS = 5000

# Chroma metadata values must be scalars, and very long answers are a sign the
# sheet holds an article rather than an answer.
MAX_ANSWER_CHARS = 4000


class SheetError(ValueError):
    """The uploaded sheet cannot be indexed, with a reason worth showing."""


@dataclass
class IngestResult:
    documents_indexed: int   # FAQ entries (sheet rows) — what the customer counts
    vectors_indexed: int     # embedded phrasings — always >= documents_indexed
    skipped_rows: int
    warnings: list[str]
    categories: list[str]


def _read_dataframe(filename: str, data: bytes) -> pd.DataFrame:
    name = (filename or "").lower()
    try:
        if name.endswith(".csv"):
            return pd.read_csv(io.BytesIO(data))
        if name.endswith((".xlsx", ".xlsm")):
            return pd.read_excel(io.BytesIO(data), engine="openpyxl")
    except Exception as exc:  # pandas raises a zoo of parser errors
        raise SheetError(f"Could not read the file: {exc}") from exc

    raise SheetError("Upload a .xlsx or .csv file.")


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Match columns case- and space-insensitively, then rename to canonical."""
    canonical = {c.lower().replace(" ", "_"): c for c in
                 (*REQUIRED_SHEET_COLUMNS, *OPTIONAL_SHEET_COLUMNS)}

    renames: dict[str, str] = {}
    for column in df.columns:
        key = str(column).strip().lower().replace(" ", "_")
        if key in canonical:
            renames[column] = canonical[key]
    return df.rename(columns=renames)


def _clean(value: Any) -> str:
    """Blank out pandas NaN and stringify everything else."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def ingest_sheet(
    *,
    collection_name: str,
    filename: str,
    data: bytes,
) -> IngestResult:
    """
    Index an uploaded sheet into *collection_name*, replacing what was there.

    Replacing rather than merging is deliberate: the sheet is the source of
    truth, so a re-upload should be able to *remove* an answer, which a merge
    could never do.
    """
    df = _normalise_columns(_read_dataframe(filename, data))

    missing = [c for c in REQUIRED_SHEET_COLUMNS if c not in df.columns]
    if missing:
        raise SheetError(
            f"The sheet is missing the {', '.join(missing)} column"
            f"{'s' if len(missing) > 1 else ''}. "
            f"Required columns: {', '.join(REQUIRED_SHEET_COLUMNS)}. "
            f"Optional: {', '.join(OPTIONAL_SHEET_COLUMNS)}."
        )

    if len(df) > MAX_ROWS:
        raise SheetError(
            f"The sheet has {len(df)} rows; the limit is {MAX_ROWS}. "
            "Split it across multiple bots, or trim it to the questions people "
            "actually ask."
        )

    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []
    warnings: list[str] = []
    skipped = 0
    rows_indexed = 0
    seen_questions: set[str] = set()
    categories: set[str] = set()

    for idx, row in df.iterrows():
        question = _clean(row.get("Question"))
        answer = _clean(row.get("Answer"))

        # A row without both halves cannot answer anything.
        if not question or not answer:
            skipped += 1
            continue

        duplicate_key = question.lower()
        if duplicate_key in seen_questions:
            skipped += 1
            warnings.append(f'Row {idx + 2}: duplicate question "{question[:60]}" skipped.')
            continue
        seen_questions.add(duplicate_key)

        if len(answer) > MAX_ANSWER_CHARS:
            answer = answer[:MAX_ANSWER_CHARS]
            warnings.append(
                f"Row {idx + 2}: answer truncated to {MAX_ANSWER_CHARS} characters."
            )

        alt = _clean(row.get("Alt_Phrasings"))
        category = _clean(row.get("Category"))
        if category:
            categories.add(category)

        metadata = {
            "question": question,
            "answer": answer,
            "category": category,
            "alt_phrasings": alt,
        }

        # Index the question and each alternate phrasing as *separate* vectors
        # pointing at the same answer, rather than concatenating them into one.
        # Concatenating drags the embedding away from all of its phrasings at
        # once: asking a sheet's question word-for-word scored only ~0.76
        # against a combined vector, which reads as a weak match when it is
        # actually a perfect one. One vector per phrasing scores each on its
        # own merits.
        phrasings = [question]
        phrasings.extend(
            phrase for phrase in (p.strip() for p in alt.split(";")) if phrase
        )

        rows_indexed += 1
        for variant, phrase in enumerate(phrasings):
            ids.append(f"doc_{idx}_{variant}")
            texts.append(phrase)
            metadatas.append(metadata)

    if not ids:
        raise SheetError(
            "No usable rows found — every row needs both a Question and an Answer."
        )

    if skipped:
        warnings.insert(
            0, f"{skipped} row{'s' if skipped != 1 else ''} skipped (blank or duplicate)."
        )

    embeddings = embed_batch(texts)

    collection = reset_collection(collection_name)
    add_documents(
        collection,
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )

    # Long warning lists are noise; the count in the first entry covers the rest.
    return IngestResult(
        documents_indexed=rows_indexed,
        vectors_indexed=len(ids),
        skipped_rows=skipped,
        warnings=warnings[:10],
        categories=sorted(categories),
    )
