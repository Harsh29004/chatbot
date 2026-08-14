"""
Ingest ``partner_faq.xlsx`` into the ChromaDB ``partner_faq_index``
collection.

Mirrors the customer ingest script — separate collection, separate data.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from shared.config import PARTNER_COLLECTION
from shared.embeddings import embed_batch
from shared.vector_store import add_documents, reset_collection

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_XLSX = DATA_DIR / "partner_faq.xlsx"


def ingest(xlsx_path: Path | None = None) -> int:
    """
    Read the Excel sheet, embed each row, and upsert into ChromaDB.

    Returns the number of documents indexed.
    """
    path = xlsx_path or DEFAULT_XLSX
    df = pd.read_excel(path, engine="openpyxl")

    df.columns = [c.strip() for c in df.columns]

    required = {"Question", "Alt_Phrasings", "Category", "Answer"}
    if not required.issubset(set(df.columns)):
        raise ValueError(
            f"Excel sheet must contain columns: {required}. "
            f"Found: {set(df.columns)}"
        )

    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict] = []

    for idx, row in df.iterrows():
        doc_id = f"partner_faq_{idx}"
        question = str(row["Question"]).strip()
        alt = str(row.get("Alt_Phrasings", "")).strip()
        category = str(row.get("Category", "")).strip()
        answer = str(row["Answer"]).strip()

        searchable = f"{question} {alt} {category}"

        ids.append(doc_id)
        texts.append(searchable)
        metadatas.append(
            {
                "question": question,
                "answer": answer,
                "category": category,
                "alt_phrasings": alt,
            }
        )

    embeddings = embed_batch(texts)

    collection = reset_collection(PARTNER_COLLECTION)
    add_documents(
        collection,
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )

    return len(ids)


if __name__ == "__main__":
    count = ingest()
    print(f"Ingested {count} partner FAQ documents.")
