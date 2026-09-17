"""
The MongoDB connection, and the small number of conventions every store follows.

This replaces the thread-local SQLite handle that used to live in eight
different modules. One client, shared: PyMongo's ``MongoClient`` owns a
connection pool and is thread-safe, so creating one per module — the shape the
SQLite code needed — would be eight pools to the same cluster for no reason.

Two conventions the whole codebase depends on
---------------------------------------------

**Ids are ObjectIds in the database and strings everywhere else.** A document
stores ``_id: ObjectId(...)``; :func:`document` hands callers back ``id`` as a
24-character string. Nothing above this module should ever see an ObjectId,
because the moment one reaches a Pydantic model or a JSON response it becomes
a serialisation error at the worst possible time.

**Indexes are declared, not created ad hoc.** Every store registers its indexes
with :func:`register_indexes` at import, and :func:`ensure_indexes` applies them
once at startup. Uniqueness that used to be a ``UNIQUE`` column is a unique
index here and is enforced by the server exactly the same way — which matters,
because two of them (one account per mailbox, one referral payout per invoice)
are load-bearing rather than tidy.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Mapping

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError  # re-exported: stores raise on it

logger = logging.getLogger(__name__)

__all__ = [
    "DuplicateKeyError",
    "atomic",
    "coll",
    "database",
    "document",
    "documents",
    "ensure_indexes",
    "get_client",
    "is_object_id",
    "object_id",
    "register_indexes",
    "reset_client",
    "to_object_id",
]

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "nexora")

# Atlas over the public internet is not a local socket; a request should fail
# with a readable error rather than hang a worker until the client gives up.
_SERVER_SELECTION_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "8000"))

_client: MongoClient | None = None
_index_registry: dict[str, list[tuple[Any, dict[str, Any]]]] = {}


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_client() -> MongoClient:
    """
    The process-wide client, created on first use.

    Lazy rather than at import so that a missing or wrong ``MONGO_URI`` fails
    when something actually needs the database — not while the test suite is
    collecting, or while a CLI script that never touches Mongo starts up.
    """
    global _client
    if _client is None:
        _client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS,
            tz_aware=False,
            appname="nexora",
        )
        logger.info("MongoDB client created for database %r.", MONGO_DB_NAME)
    return _client


def reset_client(client: MongoClient | None = None) -> None:
    """
    Replace the shared client, closing the old one.

    Exists for tests, which point every store at a throwaway database between
    cases. Passing ``None`` drops the client so the next call rebuilds it from
    the current environment.
    """
    global _client
    if _client is not None and client is not _client:
        try:
            _client.close()
        except Exception:  # pragma: no cover - closing a dead client
            pass
    _client = client


def database() -> Database:
    return get_client()[MONGO_DB_NAME]


def coll(name: str) -> Collection:
    """The named collection. Every store goes through here, never through a
    module-level handle — a cached ``Collection`` would keep pointing at the
    old database after :func:`reset_client`."""
    return database()[name]


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------

def object_id(value: Any) -> ObjectId | None:
    """
    Coerce a value to an ObjectId, or ``None`` when it cannot be one.

    ``None`` rather than an exception because almost every caller is turning a
    path parameter or a query string into a lookup, and "no such id" and "not
    a valid id" deserve the same 404. A store that needs the distinction can
    check :func:`is_object_id` first.
    """
    if isinstance(value, ObjectId):
        return value
    if value is None:
        return None
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError, ValueError):
        return None


def to_object_id(value: Any) -> ObjectId:
    """Like :func:`object_id`, but for internal callers that know it is one."""
    oid = object_id(value)
    if oid is None:
        raise ValueError(f"{value!r} is not a valid id.")
    return oid


def is_object_id(value: Any) -> bool:
    return object_id(value) is not None


def document(doc: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """
    A stored document as the rest of the codebase wants it.

    ``_id`` becomes a string ``id``, and every ObjectId field alongside it
    (``customer_id``, ``user_id``, ``referral_id`` …) becomes a string too. The
    layers above this one deal in JSON-safe values only, so the conversion
    belongs here rather than in thirty call sites that each have to remember.
    """
    if doc is None:
        return None

    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            out["id"] = str(value) if isinstance(value, ObjectId) else value
        elif isinstance(value, ObjectId):
            out[key] = str(value)
        elif isinstance(value, list):
            out[key] = [str(v) if isinstance(v, ObjectId) else v for v in value]
        else:
            out[key] = value
    return out


def documents(docs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in (document(doc) for doc in docs) if d is not None]


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

def register_indexes(collection: str, specs: list[tuple[Any, dict[str, Any]]]) -> None:
    """
    Declare the indexes a collection needs.

    Called at module import so the declaration sits next to the queries that
    rely on it, and applied later by :func:`ensure_indexes`. Registering twice
    for the same collection replaces the previous list rather than appending,
    which keeps a re-imported module from stacking duplicates.
    """
    _index_registry[collection] = specs


# Every module that registers indexes. Imported by ensure_indexes before it
# applies anything, because registration happens at import: without this, the
# indexes that get created depend on which modules the caller happened to have
# imported first, and the one most likely to be missing is the one nothing has
# touched yet. A silently absent unique index is not a missing optimisation,
# it is a missing constraint.
_INDEX_OWNING_MODULES = (
    "backend.shared.api_keys",
    "backend.shared.logging_store",
    "backend.billing.db",
    "backend.assistant.store",
    "backend.support.store",
    "bot.store",
    "bot.catalogue",
    "backend.shared.rate_limits",
    "backend.shared.vector_store",
    "backend.shared.llm",
)


def ensure_indexes() -> dict[str, int]:
    """
    Apply every registered index. Safe to run on each startup.

    ``create_index`` is idempotent for an identical spec, and raises when a
    name exists with *different* options — which is the useful failure, since
    it means a deploy changed an index definition and somebody has to decide
    what happens to the old one.
    """
    import importlib

    for module in _INDEX_OWNING_MODULES:
        importlib.import_module(module)

    applied: dict[str, int] = {}
    for name, specs in _index_registry.items():
        collection = coll(name)
        for keys, options in specs:
            collection.create_index(keys, **options)
        applied[name] = len(specs)
    logger.info("Ensured indexes on %d collections.", len(applied))
    return applied


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

# Whether the connected deployment can run multi-document transactions. Atlas
# and any replica set can; a standalone mongod and mongomock cannot. Probed
# once on first use rather than assumed, because the answer differs between a
# developer's laptop and production and neither should have to configure it.
_transactions_supported: bool | None = None


def _supports_transactions() -> bool:
    global _transactions_supported
    if _transactions_supported is None:
        try:
            info = get_client().server_info()
            hello = get_client().admin.command("hello")
            _transactions_supported = bool(
                hello.get("setName") or hello.get("msg") == "isdbgrid"
            ) and int(str(info.get("version", "0")).split(".")[0]) >= 4
        except Exception:
            # Includes mongomock, which raises on `hello`. A test double that
            # cannot do transactions is not a reason to fail the write.
            _transactions_supported = False
        if not _transactions_supported:
            logger.info(
                "MongoDB deployment does not support transactions — multi-document "
                "writes will be applied in order instead of atomically."
            )
    return _transactions_supported


@contextmanager
def atomic() -> Iterator[Any]:
    """
    Run a block of writes in one transaction where the deployment allows it.

    Yields the session to pass to each write, or ``None`` when transactions are
    unavailable — PyMongo accepts ``session=None`` everywhere, so the calling
    code is identical either way and does not sprout a branch per write.

    This is deliberately quiet about the difference. The writes that use it
    (spending credits, paying a referral) are already ordered so that the
    worst a partial failure can do is under-charge or leave a reward unpaid,
    both of which are recoverable and neither of which loses money. On a
    replica set they cannot partially apply at all.
    """
    if not _supports_transactions():
        yield None
        return

    with get_client().start_session() as session:
        with session.start_transaction():
            yield session
