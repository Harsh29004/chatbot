"""
Turn *whatever the customer uploaded* into a Question/Answer table.

The rest of the pipeline only ever wants four columns. What arrives, though,
is whatever a support team happens to keep its answers in: a spreadsheet, a
CSV export, a JSON dump from a helpdesk, a JSONL fine-tuning set, a
hand-written FAQ in Markdown, a page saved from a website. Accepting only two
extensions turned a five-minute setup into "first, reformat your data".

So this module is deliberately forgiving in two directions at once:

* **Format** — the file is dispatched on its extension and, when the extension
  is missing or lies, on its actual bytes. Anything that cannot be parsed as a
  table is read as prose and mined for question/answer pairs.
* **Naming** — columns go through an alias table, so ``prompt``/``completion``,
  ``q``/``a`` and ``user``/``assistant`` all land on ``Question``/``Answer``.

Everything here raises :class:`SheetError` with a message written for the
person who made the file, never a parser traceback.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

import pandas as pd

from bot.templates import OPTIONAL_SHEET_COLUMNS, REQUIRED_SHEET_COLUMNS


class SheetError(ValueError):
    """The uploaded file cannot be indexed, with a reason worth showing."""


# What we tell people we accept. Kept as data because it is shown in error
# messages, in the API's capability payload, and in the upload widget.
SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".csv", ".tsv", ".psv", ".txt", ".text", ".tab",
    ".xlsx", ".xlsm", ".xls", ".ods",
    ".json", ".jsonl", ".ndjson",
    ".yaml", ".yml",
    ".xml", ".html", ".htm",
    ".md", ".markdown", ".rst",
    ".parquet",
    ".pdf", ".docx",
    ".zip",
)

# Extensions we decode as text before dispatching. Everything else is binary,
# or is sniffed.
_TEXT_SUFFIXES = (
    ".csv", ".tsv", ".psv", ".txt", ".text", ".tab",
    ".json", ".jsonl", ".ndjson", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".md", ".markdown", ".rst",
)
_BINARY_SUFFIXES = (
    ".xlsx", ".xlsm", ".xls", ".ods", ".parquet", ".pdf", ".docx", ".zip",
)


# ---------------------------------------------------------------------------
# Column naming
# ---------------------------------------------------------------------------

def _key(name: Any) -> str:
    """Fold a column name to a comparison key: ``"Alt Phrasings" -> alt_phrasings``."""
    text = str(name).strip().lower()
    # json_normalize produces "data.question"; only the leaf carries meaning.
    text = text.rsplit(".", 1)[-1]
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


# Names we are confident about: if a column is called one of these, that is
# what it is.
_STRONG_ALIASES: dict[str, str] = {
    **{_key(c): c for c in (*REQUIRED_SHEET_COLUMNS, *OPTIONAL_SHEET_COLUMNS)},

    "question": "Question", "questions": "Question", "q": "Question",
    "prompt": "Question", "query": "Question", "ask": "Question",
    "user": "Question", "user_message": "Question", "user_input": "Question",
    "human": "Question", "input": "Question", "instruction": "Question",
    "utterance": "Question", "intent": "Question", "trigger": "Question",
    "faq": "Question", "faq_question": "Question",

    "answer": "Answer", "answers": "Answer", "a": "Answer",
    "completion": "Answer", "response": "Answer", "reply": "Answer",
    "assistant": "Answer", "assistant_message": "Answer", "bot": "Answer",
    "bot_reply": "Answer", "gpt": "Answer", "solution": "Answer",
    "resolution": "Answer", "faq_answer": "Answer",

    "alt_phrasings": "Alt_Phrasings", "alt": "Alt_Phrasings",
    "alternate_phrasings": "Alt_Phrasings",
    "alternative_phrasings": "Alt_Phrasings", "phrasings": "Alt_Phrasings",
    "variants": "Alt_Phrasings", "variations": "Alt_Phrasings",
    "synonyms": "Alt_Phrasings", "aliases": "Alt_Phrasings",
    "paraphrases": "Alt_Phrasings", "similar_questions": "Alt_Phrasings",
    "keywords": "Alt_Phrasings",

    "category": "Category", "categories": "Category", "topic": "Category",
    "section": "Category", "group": "Category", "tag": "Category",
    "tags": "Category", "department": "Category",
}

# Names that *might* be what we want. Only consulted when nothing stronger
# claimed the slot, because a column called "text" is the answer in a helpdesk
# export and a footnote in a spreadsheet.
_WEAK_ALIASES: dict[str, str] = {
    "title": "Question", "subject": "Question", "name": "Question",
    "key": "Question", "label": "Question",

    "output": "Answer", "content": "Answer", "text": "Answer",
    "body": "Answer", "message": "Answer", "description": "Answer",
    "value": "Answer", "result": "Answer", "details": "Answer",
}

_CANONICAL: tuple[str, ...] = (*REQUIRED_SHEET_COLUMNS, *OPTIONAL_SHEET_COLUMNS)


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename whatever the file called its columns to the four canonical names.

    Resolution runs strongest-first, so a sheet carrying both ``Question`` and
    ``Title`` keeps the real question. Each canonical name is claimed at most
    once and the leftmost claimant wins, which on real exports is the primary
    field.
    """
    claimed: dict[str, Any] = {}

    for aliases in (_STRONG_ALIASES, _WEAK_ALIASES):
        for column in df.columns:
            if column in claimed.values():
                continue
            canonical = aliases.get(_key(column))
            if canonical and canonical not in claimed:
                claimed[canonical] = column

    renamed = df.rename(columns={o: c for c, o in claimed.items()})

    # A rename can collide with a column that already had that name (a sheet
    # with both "Answer" and "Response"); keep the first of each.
    return renamed.loc[:, ~renamed.columns.duplicated()]


def _header_looks_like_data(columns: list[str]) -> bool:
    """
    Decide whether a headerless file's first row was eaten as column names.

    Headers are short labels; content is a sentence. A cell that asks a
    question or runs long is content, and losing a real FAQ entry is worse
    than carrying one junk row we cannot classify.
    """
    return any(c.rstrip().endswith("?") or len(c) > 60 for c in columns)


def _positional_fallback(df: pd.DataFrame) -> pd.DataFrame:
    """
    Last resort for a table whose columns are named nothing we recognise.

    Only reached when *neither* Question nor Answer resolved — the shape of a
    headerless two-column export — so mapping by position is the only reading
    left. Columns land in the documented sheet order.
    """
    if df.shape[1] < 2:
        return df

    header = [str(c) for c in df.columns]
    frame = df
    if _header_looks_like_data(header):
        first_row = pd.DataFrame([header], columns=df.columns)
        frame = pd.concat([first_row, df.astype(object)], ignore_index=True)

    names = list(_CANONICAL)[: frame.shape[1]]
    names += [f"extra_{i}" for i in range(frame.shape[1] - len(names))]
    frame = frame.copy()
    frame.columns = names
    return frame


def _stringify(value: Any, column: str) -> Any:
    """Flatten the containers JSON and YAML files put inside a single cell."""
    if isinstance(value, (list, tuple, set)):
        parts = [str(v).strip() for v in value if str(v).strip()]
        # Alt_Phrasings is split on ";" downstream, so it has to join on ";".
        return "; ".join(parts) if column == "Alt_Phrasings" else "\n".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def finalise(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise names, fall back to position, then flatten nested cells."""
    frame = normalise_columns(df)

    if not any(c in frame.columns for c in REQUIRED_SHEET_COLUMNS):
        frame = normalise_columns(_positional_fallback(frame))

    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].map(lambda v, c=column: _stringify(v, c))
    return frame


# ---------------------------------------------------------------------------
# Bytes -> text
# ---------------------------------------------------------------------------

def _decode(data: bytes) -> str:
    """
    Decode without being precious about it.

    Excel writes UTF-16 CSVs with a BOM, Windows tools write cp1252, everything
    else writes UTF-8. Latin-1 is the terminal fallback because it cannot fail:
    a garbled character the customer can see and fix beats a 400 whose only
    content is "codec error".
    """
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Tabular formats
# ---------------------------------------------------------------------------

_DELIMITERS = (",", "\t", ";", "|")


def _read_delimited(text: str) -> pd.DataFrame:
    """
    Parse delimiter-separated text, sniffing the delimiter rather than
    assuming a comma — a large share of the world's "CSV"s are semicolon- or
    tab-separated because that is what the exporting tool wrote.
    """
    attempts: list[pd.DataFrame] = []
    try:
        attempts.append(pd.read_csv(io.StringIO(text), sep=None, engine="python"))
    except Exception:
        pass

    if not attempts or attempts[0].shape[1] < 2:
        for delimiter in _DELIMITERS:
            try:
                attempts.append(pd.read_csv(io.StringIO(text), sep=delimiter))
            except Exception:
                continue

    usable = [df for df in attempts if df.shape[1] > 1 and not df.empty]
    if usable:
        return max(usable, key=lambda df: df.shape[1])
    if attempts:
        return attempts[0]
    raise SheetError("Could not read the file as a table.")


def _read_excel(data: bytes, suffix: str) -> pd.DataFrame:
    """
    Read *every* tab in the workbook, not just the first.

    People keep FAQs one tab per department. Tabs without usable columns are
    dropped rather than fatal, so a workbook with a "Notes" tab still imports.
    """
    engines = {".xlsx": "openpyxl", ".xlsm": "openpyxl", ".xls": "xlrd", ".ods": "odf"}
    engine = engines.get(suffix, "openpyxl")
    try:
        sheets = pd.read_excel(io.BytesIO(data), engine=engine, sheet_name=None)
    except ImportError as exc:
        raise SheetError(
            f"Reading {suffix} files needs an extra package that isn't installed "
            f"({exc}). Save the file as .xlsx or .csv and upload that."
        ) from exc
    except Exception as exc:
        raise SheetError(f"Could not read the workbook: {exc}") from exc

    frames = []
    for sheet in sheets.values():
        if sheet.empty:
            continue
        candidate = normalise_columns(sheet)
        if all(c in candidate.columns for c in REQUIRED_SHEET_COLUMNS):
            frames.append(candidate)

    if frames:
        return pd.concat(frames, ignore_index=True)

    # No tab matched. Hand back the first non-empty one so the caller can give
    # the usual "missing column" message naming what it actually found.
    for sheet in sheets.values():
        if not sheet.empty:
            return sheet
    raise SheetError("The workbook is empty.")


def _read_parquet(data: bytes) -> pd.DataFrame:
    try:
        return pd.read_parquet(io.BytesIO(data))
    except ImportError as exc:
        raise SheetError(
            "Reading .parquet needs pyarrow, which isn't installed. "
            "Export the data as .csv and upload that."
        ) from exc
    except Exception as exc:
        raise SheetError(f"Could not read the parquet file: {exc}") from exc


# ---------------------------------------------------------------------------
# Structured formats: JSON, JSONL, YAML, XML
# ---------------------------------------------------------------------------

# Keys a JSON document commonly wraps its actual list of entries in.
_CONTAINER_KEYS = (
    "faqs", "faq", "data", "items", "rows", "records", "entries",
    "questions", "qa", "qas", "pairs", "results", "documents", "list",
    "conversations", "messages",
)

_ROLE_KEYS = ("role", "from", "speaker", "sender", "author")
_CONTENT_KEYS = ("content", "text", "message", "value", "body")
_ASK_ROLES = {"user", "human", "customer", "question", "prompter", "client"}
_ANSWER_ROLES = {"assistant", "bot", "gpt", "agent", "ai", "answer"}


def _turns_to_pairs(turns: list[Any]) -> list[dict[str, str]]:
    """
    Fold a chat transcript into Q/A rows.

    Chat-shaped JSONL (the OpenAI and ShareGPT formats) is one of the commoner
    ways a team already has its answers written down, so it is worth
    understanding rather than rejecting.
    """
    pairs: list[dict[str, str]] = []
    pending: str | None = None

    for turn in turns:
        if not isinstance(turn, dict):
            continue
        role = next((str(turn[k]).strip().lower() for k in _ROLE_KEYS if turn.get(k)), "")
        content = next((str(turn[k]).strip() for k in _CONTENT_KEYS if turn.get(k)), "")
        if not content:
            continue
        if role in _ASK_ROLES:
            pending = content
        elif role in _ANSWER_ROLES and pending:
            pairs.append({"Question": pending, "Answer": content})
            pending = None
    return pairs


def _transcript_in(record: dict) -> list[Any] | None:
    for key in ("messages", "conversations", "conversation", "turns", "dialog", "dialogue"):
        value = record.get(key)
        if isinstance(value, list) and any(isinstance(v, dict) for v in value):
            return value
    return None


def _frame_from_object(obj: Any, depth: int = 0) -> pd.DataFrame:
    """Coerce a decoded JSON/YAML document into a table, whatever its shape."""
    if depth > 5:
        raise SheetError("The file is nested more deeply than we can unpack.")

    if isinstance(obj, dict):
        # {"faqs": [...]} and friends — unwrap and recurse.
        for key in _CONTAINER_KEYS:
            value = obj.get(key)
            if isinstance(value, (list, dict)) and value:
                if key in ("messages", "conversations") and isinstance(value, list):
                    pairs = _turns_to_pairs(value)
                    if pairs:
                        return pd.DataFrame(pairs)
                return _frame_from_object(value, depth + 1)

        # An object holding exactly one list *is* that list.
        lists = [v for v in obj.values() if isinstance(v, list) and v]
        if len(lists) == 1 and all(isinstance(v, dict) for v in lists[0]):
            return _frame_from_object(lists[0], depth + 1)

        # {"Do you deliver?": "Yes, city-wide."} — the simplest FAQ file there is.
        if obj and all(not isinstance(v, (dict, list)) for v in obj.values()):
            return pd.DataFrame(
                {"Question": [str(k) for k in obj],
                 "Answer": [str(v) for v in obj.values()]}
            )

        # {"Do you deliver?": {"answer": ..., "category": ...}}
        if obj and all(isinstance(v, dict) for v in obj.values()):
            return pd.json_normalize([{"Question": k, **v} for k, v in obj.items()])

        return pd.json_normalize([obj])

    if isinstance(obj, list):
        if not obj:
            raise SheetError("The file contains no entries.")

        if all(isinstance(item, dict) for item in obj):
            transcripts = [t for item in obj if (t := _transcript_in(item)) is not None]
            if len(transcripts) == len(obj):
                pairs = [p for turns in transcripts for p in _turns_to_pairs(turns)]
                if pairs:
                    return pd.DataFrame(pairs)
            return pd.json_normalize(obj, max_level=1)

        # A bare list of chat turns.
        pairs = _turns_to_pairs(obj)
        if pairs:
            return pd.DataFrame(pairs)

        # [["Do you deliver?", "Yes."], ...] — positional pairs.
        if all(isinstance(item, (list, tuple)) for item in obj):
            width = max(len(item) for item in obj)
            names = list(_CANONICAL)[:width]
            names += [f"extra_{i}" for i in range(width - len(names))]
            return pd.DataFrame(
                [list(item) + [""] * (width - len(item)) for item in obj],
                columns=names,
            )

        if all(isinstance(item, str) for item in obj):
            return _frame_from_text("\n".join(obj))

    raise SheetError("The file's structure isn't a list of question/answer entries.")


def _read_json(text: str) -> pd.DataFrame:
    stripped = text.strip()
    if not stripped:
        raise SheetError("The file is empty.")
    try:
        return _frame_from_object(json.loads(stripped))
    except json.JSONDecodeError:
        # A .json file holding one object per line is common enough to retry as
        # JSONL before giving up.
        return _read_jsonl(stripped)


def _read_jsonl(text: str) -> pd.DataFrame:
    records: list[Any] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip().rstrip(",")
        if not line or line in ("[", "]"):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SheetError(f"Line {number} is not valid JSON: {exc.msg}.") from exc

    if not records:
        raise SheetError("The file contains no entries.")
    return _frame_from_object(records)


def _read_yaml(text: str) -> pd.DataFrame:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML ships with our deps
        raise SheetError(
            "Reading YAML needs PyYAML, which isn't installed. Upload JSON or CSV."
        ) from exc
    try:
        loaded = yaml.safe_load(text)
    except Exception as exc:
        raise SheetError(f"Could not read the YAML: {exc}") from exc
    if loaded is None:
        raise SheetError("The file is empty.")
    return _frame_from_object(loaded)


def _read_xml(text: str) -> pd.DataFrame:
    """Flatten an XML export: every element with leaf children becomes a row."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SheetError(f"Could not read the XML: {exc}") from exc

    rows: list[dict[str, str]] = []
    for node in root.iter():
        children = list(node)
        if not children:
            continue
        row: dict[str, str] = {str(k): str(v) for k, v in node.attrib.items()}
        for child in children:
            if list(child):  # a container, not a field
                continue
            row[str(child.tag)] = (child.text or "").strip()
            row.update({str(k): str(v) for k, v in child.attrib.items()})
        if len(row) >= 2:
            rows.append(row)

    if not rows:
        raise SheetError("No question/answer elements found in the XML.")
    return pd.DataFrame(rows)


def _read_html(text: str) -> pd.DataFrame:
    """
    Prefer a real ``<table>``; otherwise strip the tags and read the page as
    prose, because a support page saved from a browser is almost always
    headings and paragraphs — a shape the text reader already understands.
    """
    try:
        tables = pd.read_html(io.StringIO(text))
    except Exception:
        tables = []

    for table in tables:
        candidate = normalise_columns(table)
        if all(c in candidate.columns for c in REQUIRED_SHEET_COLUMNS):
            return candidate

    try:
        from bs4 import BeautifulSoup

        plain = BeautifulSoup(text, "html.parser").get_text("\n")
    except ImportError:
        plain = re.sub(r"<[^>]+>", "\n", text)

    plain = re.sub(r"\n{3,}", "\n\n", plain)
    if tables and not plain.strip():
        return tables[0]
    return _frame_from_text(plain)


# ---------------------------------------------------------------------------
# Documents: PDF, DOCX
# ---------------------------------------------------------------------------

def _read_pdf(data: bytes) -> pd.DataFrame:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SheetError(
            "Reading PDFs needs the pypdf package, which isn't installed. "
            "Copy the FAQ into a .csv, .txt or .docx and upload that."
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        raise SheetError(f"Could not read the PDF: {exc}") from exc
    if not text.strip():
        raise SheetError(
            "That PDF has no selectable text — it is probably a scan. "
            "Upload the FAQ as a sheet or a text file instead."
        )
    return _frame_from_text(text)


def _read_docx(data: bytes) -> pd.DataFrame:
    """
    Read a Word file without a Word library: a .docx is a zip of XML, and the
    paragraph text is one regex away.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception as exc:
        raise SheetError(f"Could not read the Word file: {exc}") from exc

    # Each <w:p> is a paragraph; each <w:t> inside it is a run of text.
    paragraphs = []
    for block in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.DOTALL):
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", block, flags=re.DOTALL)
        paragraphs.append(_unescape_xml(re.sub(r"<[^>]+>", "", "".join(runs)).strip()))

    text = "\n".join(paragraphs)
    if not text.strip():
        raise SheetError("The Word file has no text in it.")
    return _frame_from_text(text)


def _unescape_xml(text: str) -> str:
    return (text.replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))


# ---------------------------------------------------------------------------
# Prose: TXT, Markdown, and anything else that ends up as text
# ---------------------------------------------------------------------------

_LABELLED = re.compile(
    r"^\s*(?:[-*>\s]*)(?:\*\*)?(q(?:uestion)?|a(?:nswer)?)\s*\d*(?:\*\*)?\s*[:.)\-]\s*(.*)$",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_INLINE_SPLIT = re.compile(r"\s*(?:\t|\|{1,2}|::|=>|->)\s*")


def _pairs_from_labels(lines: list[str]) -> list[dict[str, str]]:
    """``Q: …`` / ``A: …`` blocks, the format most hand-written FAQs use."""
    pairs: list[dict[str, str]] = []
    current: str | None = None
    question: str | None = None
    answer: list[str] = []

    def flush() -> None:
        if question and answer:
            pairs.append({"Question": question, "Answer": " ".join(answer).strip()})

    for line in lines:
        match = _LABELLED.match(line)
        if match:
            label, rest = match.group(1).lower()[0], match.group(2).strip()
            if label == "q":
                flush()
                question, answer, current = rest, [], "q"
            else:
                answer, current = ([rest] if rest else []), "a"
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if current == "a":
            answer.append(stripped)
        elif current == "q" and question is not None:
            question = f"{question} {stripped}".strip()

    flush()
    return pairs


def _pairs_from_headings(lines: list[str]) -> list[dict[str, str]]:
    """Markdown headings as questions, the text beneath them as the answer."""
    pairs: list[dict[str, str]] = []
    question: str | None = None
    body: list[str] = []

    def flush() -> None:
        if question and body:
            text = "\n".join(body).strip()
            if text:
                pairs.append({"Question": question, "Answer": text})

    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            flush()
            question, body = heading.group(2).strip(), []
            continue
        if question is not None and line.strip():
            body.append(line.strip())

    flush()
    return pairs


def _pairs_from_inline(lines: list[str]) -> list[dict[str, str]]:
    """One pair per line, separated by a tab, ``|``, ``::`` or ``->``."""
    pairs: list[dict[str, str]] = []
    for line in lines:
        if not line.strip():
            continue
        parts = [p.strip() for p in _INLINE_SPLIT.split(line.strip()) if p.strip()]
        if len(parts) < 2 or set(parts[0]) <= set("-=| "):
            continue
        pairs.append({"Question": parts[0], "Answer": parts[1]})
    return pairs


def _pairs_from_question_marks(lines: list[str]) -> list[dict[str, str]]:
    """
    A line ending in "?" opens a question and everything until the next one is
    its answer — what an FAQ pasted out of a web page looks like.
    """
    pairs: list[dict[str, str]] = []
    question: str | None = None
    body: list[str] = []

    def flush() -> None:
        if question and body:
            pairs.append({"Question": question, "Answer": " ".join(body).strip()})

    for line in lines:
        stripped = line.strip().lstrip("-*#• ").strip()
        if not stripped:
            continue
        # Two characters is enough to be a real question ("Refunds?"); a
        # bare "?" on its own line is punctuation left over from a layout.
        if stripped.endswith("?") and len(stripped) > 2:
            flush()
            question, body = stripped, []
        elif question:
            body.append(stripped)

    flush()
    return pairs


def _pairs_from_blocks(text: str) -> list[dict[str, str]]:
    """Blank-line-separated blocks: first line the question, the rest the answer."""
    pairs: list[dict[str, str]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) >= 2:
            pairs.append({"Question": lines[0], "Answer": " ".join(lines[1:])})
    return pairs


def _markdown_table(text: str) -> pd.DataFrame | None:
    """Pull a pipe table out of Markdown, dropping its ``|---|`` separator row."""
    rows = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(rows) < 2:
        return None
    body = "\n".join(
        row.strip("|") for row in rows if not re.fullmatch(r"\|[\s:|-]+\|?", row)
    )
    try:
        frame = pd.read_csv(io.StringIO(body), sep="|", skipinitialspace=True)
    except Exception:
        return None
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame if frame.shape[1] > 1 else None


def _frame_from_text(text: str) -> pd.DataFrame:
    """
    Read prose as an FAQ, trying the least ambiguous shape first.

    Order matters: an explicit ``Q:``/``A:`` label means what it says, while
    "the line ends in a question mark" is a guess, so it is asked last.
    Whichever strategy finds pairs first wins — combining them would
    double-count a document that satisfies two.
    """
    table = _markdown_table(text)
    if table is not None:
        candidate = normalise_columns(table)
        if all(c in candidate.columns for c in REQUIRED_SHEET_COLUMNS):
            return candidate

    lines = text.splitlines()
    for strategy in (
        lambda: _pairs_from_labels(lines),
        lambda: _pairs_from_headings(lines),
        lambda: _pairs_from_inline(lines),
        lambda: _pairs_from_question_marks(lines),
        lambda: _pairs_from_blocks(text),
    ):
        pairs = strategy()
        if pairs:
            return pd.DataFrame(pairs)

    raise SheetError(
        "No question/answer pairs found in that file. Write each entry as "
        '"Q: …" then "A: …", or upload a sheet with Question and Answer columns.'
    )


def _read_text_or_table(text: str) -> pd.DataFrame:
    """
    For .txt and anything unlabelled: a table if it reads as one, prose if not.

    A .txt file is as likely to be a tab-separated export as a hand-written
    FAQ, and only trying tells us which.
    """
    try:
        table = normalise_columns(_read_delimited(text))
        if not table.empty and all(c in table.columns for c in REQUIRED_SHEET_COLUMNS):
            return table
    except Exception:
        pass
    return _frame_from_text(text)


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------

def _read_zip(data: bytes) -> pd.DataFrame:
    """
    Import every readable member of a zip and stack them.

    Helpdesk exports arrive as archives, and unzipping first is exactly the
    chore this module exists to remove. Unreadable members are skipped rather
    than fatal — an archive is expected to contain incidental files.
    """
    frames: list[pd.DataFrame] = []
    problems: list[str] = []

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SheetError(f"That archive is corrupt: {exc}") from exc

    with archive:
        for info in archive.infolist():
            name = info.filename
            lowered = name.lower()
            if info.is_dir() or name.startswith(("__MACOSX/", ".")):
                continue
            if not lowered.endswith(SUPPORTED_EXTENSIONS) or lowered.endswith(".zip"):
                continue
            try:
                frame = finalise(read_any(name, archive.read(info)))
            except Exception as exc:  # one bad member must not sink the import
                problems.append(f"{name}: {exc}")
                continue
            if all(c in frame.columns for c in REQUIRED_SHEET_COLUMNS):
                frames.append(frame)

    if not frames:
        detail = f" ({problems[0]})" if problems else ""
        raise SheetError(f"Nothing in that archive could be read as an FAQ{detail}.")
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _sniff(data: bytes, text: str | None) -> str:
    """
    Work out the format from the bytes when the name doesn't tell us.

    Files arrive named ``export`` or ``faq.dat`` often enough that guessing is
    worth doing, and a wrong extension is worse than none — so this ignores
    the name entirely and looks at what is actually there.
    """
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile as exc:
            raise SheetError(f"That file looks like a corrupt archive: {exc}") from exc
        if any(n.startswith("xl/") for n in names):
            return ".xlsx"
        if "word/document.xml" in names:
            return ".docx"
        if any(n.startswith("content.xml") for n in names):
            return ".ods"
        return ".zip"
    if data[:5] == b"%PDF-":
        return ".pdf"
    if data[:4] == b"PAR1":
        return ".parquet"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # legacy Office compound file
        return ".xls"

    head = (text or "").lstrip()[:2048]
    if head.startswith(("{", "[")):
        lines = [line for line in (text or "").splitlines() if line.strip()]
        if len(lines) > 1 and all(line.strip().startswith("{") for line in lines):
            return ".jsonl"
        return ".json"
    if head.startswith("<?xml") or (re.match(r"<[a-zA-Z]", head) and "</" in head):
        return ".html" if re.search(r"<(html|table|body|div)\b", head, re.I) else ".xml"
    if head.startswith("---") and ":" in head:
        return ".yaml"
    return ".txt"


def read_any(filename: str, data: bytes) -> pd.DataFrame:
    """
    Parse *data* into a DataFrame, choosing a reader from the file name and,
    failing that, from the bytes themselves.

    The frame still carries whatever column names the file used; run it through
    :func:`finalise` — or call :func:`load_table` — for canonical ones.
    """
    if not data:
        raise SheetError("The file is empty.")

    name = (filename or "").strip().lower()
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""

    # Binary formats are settled before we try to make text out of them.
    text: str | None = None if suffix in _BINARY_SUFFIXES else _decode(data)

    if suffix not in _BINARY_SUFFIXES and suffix not in _TEXT_SUFFIXES:
        suffix = _sniff(data, text)
        text = None if suffix in _BINARY_SUFFIXES else (text if text is not None else _decode(data))

    try:
        if suffix in (".xlsx", ".xlsm", ".xls", ".ods"):
            return _read_excel(data, suffix)
        if suffix == ".parquet":
            return _read_parquet(data)
        if suffix == ".pdf":
            return _read_pdf(data)
        if suffix == ".docx":
            return _read_docx(data)
        if suffix == ".zip":
            return _read_zip(data)

        assert text is not None
        if suffix == ".json":
            return _read_json(text)
        if suffix in (".jsonl", ".ndjson"):
            return _read_jsonl(text)
        if suffix in (".yaml", ".yml"):
            return _read_yaml(text)
        if suffix == ".xml":
            return _read_xml(text)
        if suffix in (".html", ".htm"):
            return _read_html(text)
        if suffix in (".md", ".markdown", ".rst"):
            return _frame_from_text(text)
        if suffix in (".csv", ".tsv", ".psv", ".tab"):
            return _read_delimited(text)
        return _read_text_or_table(text)
    except SheetError:
        raise
    except Exception as exc:  # parsers raise a zoo of errors; none are showable
        raise SheetError(f"Could not read the file: {exc}") from exc


def load_table(filename: str, data: bytes) -> pd.DataFrame:
    """Read any supported file and hand back canonical Question/Answer columns."""
    return finalise(read_any(filename, data))
