"""Durable local document ingestion, indexing and course-scoped retrieval.

This is an unauthenticated *single-user* application. Course filters provide logical
workspace separation, not an authorization boundary.
"""
from __future__ import annotations

import hashlib
import json
from io import BytesIO
import math
import os
import re
import sqlite3
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import httpx
from PIL import Image, UnidentifiedImageError

MAX_UPLOAD = 15 * 1024 * 1024
MAX_CHUNKS = 600
IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF"}
ALLOWED = {".pdf", ".docx", ".pptx", ".txt", ".md", *IMAGE_FORMATS}
MAX_IMAGE_PIXELS = 30_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
STOPWORDS = frozenset("the and for with from does what when where which who why how are was were is this that "
                      "about into your their can you has have had will would could should please make create "
                      "give show tell explain question questions exam lecture document notes".split())


def uploads_root() -> Path:
    from backend.app import ROOT
    return Path(os.getenv("QUESTGEN_UPLOAD_DIR", str(ROOT / "data" / "uploads")))


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            extension TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','extracting','chunking','indexing','ready','failed')),
            progress INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            warning TEXT,
            pages INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(course_id, sha256)
        );
        CREATE INDEX IF NOT EXISTS documents_course_created ON documents(course_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            page INTEGER,
            section TEXT,
            ordinal INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding TEXT
        );
        CREATE INDEX IF NOT EXISTS chunks_document ON document_chunks(document_id,ordinal);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, tokenize='unicode61');
        CREATE TABLE IF NOT EXISTS message_sources (
            message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            chunk_id TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            PRIMARY KEY(message_id,chunk_id)
        );
    """)
    # Migration: existing document rows and citations are kept but old index
    # versions are reprocessed through the only supported extraction dispatcher.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
    if "pipeline_version" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN pipeline_version INTEGER NOT NULL DEFAULT 1")
    conn.execute("""CREATE TABLE IF NOT EXISTS document_units (
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL, page INTEGER, section TEXT,
        has_native INTEGER NOT NULL, has_vision INTEGER NOT NULL,
        warning TEXT, indexed INTEGER NOT NULL,
        PRIMARY KEY(document_id,ordinal)
    )""")
    from backend import vision
    vision.ensure_tables(conn)


def document_path(document_id: str, extension: str) -> Path:
    return uploads_root() / (document_id + extension)


def validate_payload(filename: str, payload: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED:
        raise ValueError("Supported files: PDF, DOCX, PPTX, TXT, MD, PNG, JPG, WEBP, BMP, TIFF")
    if not payload or len(payload) > MAX_UPLOAD:
        raise ValueError("File must be between 1 byte and 15 MiB")
    if extension == ".pdf" and not payload.startswith(b"%PDF-"):
        raise ValueError("File is not a valid PDF")
    if extension in (".docx", ".pptx"):
        if not payload.startswith(b"PK"):
            raise ValueError(f"File is not a valid {extension[1:].upper()}")
        try:
            with ZipFile(BytesIO(payload)) as archive:
                items = archive.infolist()
                if len(items) > 2500 or sum(x.file_size for x in items) > 100 * 1024 * 1024:
                    raise ValueError(f"{extension[1:].upper()} archive exceeds extraction limits")
                required = "word/document.xml" if extension == ".docx" else "ppt/presentation.xml"
                if required not in archive.namelist() or "[Content_Types].xml" not in archive.namelist():
                    raise ValueError(f"File is not a valid {extension[1:].upper()}")
        except BadZipFile as exc:
            raise ValueError(f"File is not a valid {extension[1:].upper()}") from exc
    if extension in IMAGE_FORMATS:
        try:
            with Image.open(BytesIO(payload)) as image:
                if image.format != IMAGE_FORMATS[extension]:
                    raise ValueError("Image contents do not match the filename extension")
                check_image_dimensions(image)
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Invalid or oversized image file") from exc
    if extension in (".txt", ".md"):
        try:
            payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Text uploads must be UTF-8") from exc
    return extension



def check_image_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Image dimensions exceed the 30-megapixel limit")



def split_segments(segments: list[tuple[int | None, str | None, str]]) -> list[dict]:
    chunks: list[dict] = []
    for page, section, content in segments:
        start = 0
        while start < len(content):
            end = min(len(content), start + 1100)
            if end < len(content):
                boundary = max(content.rfind("\n", start + 550, end), content.rfind(" ", start + 550, end))
                if boundary > start:
                    end = boundary
            excerpt = content[start:end].strip()
            if excerpt:
                chunks.append({"page": page, "section": section, "content": excerpt})
                if len(chunks) > MAX_CHUNKS:
                    raise ValueError("Document has too many chunks (limit 600)")
            if end >= len(content):
                break
            start = max(start + 1, end - 140)
    return chunks


def normalized_vector(values: list[float]) -> list[float] | None:
    if not values or len(values) > 4096 or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
        return None
    magnitude = math.sqrt(sum(v * v for v in values))
    if magnitude <= 0:
        return None
    return [float(v) / magnitude for v in values]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Optional local Ollama embeddings; never uses the generation/chat model."""
    model = os.getenv("QUESTGEN_EMBED_MODEL", "").strip()
    if not model:
        return []
    # Honor the same Ollama connection and optional proxy key used in Model settings.
    from backend.app import model_credentials
    chat_url, auth_headers = model_credentials({"provider": "ollama"})
    url = chat_url.removesuffix("/v1/chat/completions") + "/api/embed"
    vectors: list[list[float]] = []
    with httpx.Client(timeout=httpx.Timeout(90, connect=5)) as client:
        for start in range(0, len(texts), 16):
            result = client.post(url, headers=auth_headers, json={"model": model, "input": texts[start:start + 16]})
            result.raise_for_status()
            batch = result.json().get("embeddings", [])
            if len(batch) != len(texts[start:start + 16]):
                raise ValueError("Embedding provider returned the wrong batch length")
            for raw in batch:
                vector = normalized_vector(raw)
                if vector is None or (vectors and len(vector) != len(vectors[0])):
                    raise ValueError("Embedding provider returned invalid vectors")
                vectors.append(vector)
    return vectors


def set_progress(doc_id: str, status: str, progress: int, *, error: str | None = None, warning: str | None = None) -> None:
    from backend.app import database, now
    with database() as conn:
        conn.execute("UPDATE documents SET status=?, progress=?, error=?, warning=?, updated_at=? WHERE id=?",
                     (status, progress, error, warning, now(), doc_id))


def ingest_document(doc_id: str) -> None:
    """Idempotent, crash-recoverable worker: all indexed rows commit together."""
    from backend.app import database, now
    with database() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        return
    path = document_path(doc_id, row["extension"])
    try:
        set_progress(doc_id, "extracting", 20)
        from backend import ingestion
        result = ingestion.extract(path, row["extension"], row["sha256"])
        segments = result.segments
        set_progress(doc_id, "chunking", 55)
        chunks = split_segments(segments)
        set_progress(doc_id, "indexing", 75)
        warning = " ".join(result.warnings)[:800] if result.warnings else None
        vectors: list[list[float]] = []
        if os.getenv("QUESTGEN_EMBED_MODEL", "").strip():
            try:
                vectors = embed_texts([chunk["content"] for chunk in chunks])
            except (httpx.HTTPError, ValueError) as exc:
                warning = " ".join(filter(None, [warning,
                    "Optional Ollama embeddings unavailable; keyword search remains active."]))
        with database() as conn:
            if conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone() is None:
                # The course may have been deleted while vision calls were in
                # flight. Drop any cache entries no surviving document needs.
                if not conn.execute("SELECT 1 FROM documents WHERE sha256=?", (row["sha256"],)).fetchone():
                    conn.execute("DELETE FROM vision_cache WHERE document_sha=?", (row["sha256"],))
                return  # deleted while indexing
            old_ids = [r[0] for r in conn.execute("SELECT rowid FROM document_chunks WHERE document_id=?", (doc_id,))]
            for rowid in old_ids:
                conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (rowid,))
            conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
            conn.execute("DELETE FROM document_units WHERE document_id=?", (doc_id,))
            for ordinal, chunk in enumerate(chunks):
                chunk_id = hashlib.sha256(f"{doc_id}:{ordinal}".encode()).hexdigest()[:32]
                cur = conn.execute(
                    "INSERT INTO document_chunks(id,document_id,course_id,page,section,ordinal,content,embedding) VALUES (?,?,?,?,?,?,?,?)",
                    (chunk_id, doc_id, row["course_id"], chunk["page"], chunk["section"], ordinal,
                     chunk["content"], json.dumps(vectors[ordinal]) if vectors else None),
                )
                conn.execute("INSERT INTO chunks_fts(rowid,content) VALUES (?,?)", (cur.lastrowid, chunk["content"]))
            for index, unit in enumerate(result.units):
                conn.execute("""INSERT INTO document_units
                    (document_id,ordinal,page,section,has_native,has_vision,warning,indexed)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (doc_id,index,unit.page,unit.section,int(unit.native),int(unit.visual),
                     unit.issue, int(bool(unit.content.strip()))))
            conn.execute("UPDATE documents SET status='ready', progress=100, error=NULL, warning=?, "
                         "pages=?,chunk_count=?,pipeline_version=2,updated_at=? WHERE id=?",
                         (warning, result.pages, len(chunks), now(), doc_id))
    except Exception as exc:
        # Detailed errors are kept server-side. No file path or embedded secrets leave the API.
        label = str(exc) if isinstance(exc, ValueError) else "Could not extract or index this document"
        set_progress(doc_id, "failed", 100, error=label[:200])
        with database() as conn:
            if not conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
                if not conn.execute("SELECT 1 FROM documents WHERE sha256=?", (row["sha256"],)).fetchone():
                    conn.execute("DELETE FROM vision_cache WHERE document_sha=?", (row["sha256"],))


def verify_course_documents(conn: sqlite3.Connection, course_id: str, ids: list[str]) -> list[str]:
    """Keep attachments in their owning course, reject incomplete or deleted files."""
    if len(ids) > 30 or len(set(ids)) != len(ids):
        raise ValueError("Select at most 30 different course materials")
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id FROM documents WHERE course_id=? AND status='ready' AND id IN ({placeholders})",
        (course_id, *ids),
    ).fetchall()
    if {row["id"] for row in rows} != set(ids):
        raise ValueError("Some selected materials are unavailable or belong to another course")
    return ids


def retrieve(course_id: str, query: str, limit: int = 6, document_ids: list[str] | None = None) -> list[dict]:
    from backend.app import database
    terms = [word for word in TOKEN_RE.findall(query.lower()) if (len(word) >= 3 or word in {"ai", "ml", "db"}) and word not in STOPWORDS][:16]
    if not terms:
        return []
    matched: dict[str, tuple[dict, float]] = {}
    if document_ids is not None and not document_ids:
        return []
    filter_sql = (" AND c.document_id IN (" + ",".join("?" for _ in document_ids) + ")") if document_ids else ""
    params: tuple = tuple(document_ids or [])
    with database() as conn:
        expression = " OR ".join('"' + item.replace('"', '') + '"' for item in terms if len(item) >= 2)
        if expression:
            rows = conn.execute("""
                SELECT c.id AS chunk_id, c.document_id, d.filename, d.extension, c.page, c.section,
                       c.content, bm25(chunks_fts) AS relevance
                FROM chunks_fts JOIN document_chunks c ON c.rowid=chunks_fts.rowid
                JOIN documents d ON d.id=c.document_id
                WHERE chunks_fts MATCH ? AND c.course_id=? AND d.status='ready' {filter_sql}
                ORDER BY relevance LIMIT 30
            """.format(filter_sql=filter_sql), (expression, course_id, *params)).fetchall()
            for rank, row in enumerate(rows):
                matched[row["chunk_id"]] = (dict(row), 1.0 / (60 + rank + 1))
        if os.getenv("QUESTGEN_EMBED_MODEL", "").strip():
            try:
                queries = embed_texts([query])
                if queries:
                    q = queries[0]
                    ranked = []
                    for row in conn.execute("""
                        SELECT c.id AS chunk_id, c.document_id, d.filename, d.extension, c.page, c.section,
                               c.content, c.embedding FROM document_chunks c
                        JOIN documents d ON d.id=c.document_id
                        WHERE c.course_id=? AND d.status='ready' AND c.embedding IS NOT NULL {filter_sql}
                        LIMIT 5000
                    """.format(filter_sql=filter_sql), (course_id, *params)):
                        vector = json.loads(row["embedding"])
                        if len(q) == len(vector):
                            score = sum(a * b for a, b in zip(q, vector))
                            if score > 0.40:
                                ranked.append((score, dict(row)))
                    ranked.sort(key=lambda r: r[0], reverse=True)
                    for rank, (_, row) in enumerate(ranked[:30]):
                        row.pop("embedding")
                        previous = matched.get(row["chunk_id"])
                        matched[row["chunk_id"]] = (row, (previous[1] if previous else 0) + 1 / (60 + rank + 1))
            except (httpx.HTTPError, ValueError, json.JSONDecodeError):
                pass  # Embedding endpoint is optional; lexical search stays available.
    answer = []
    for row, score in sorted(matched.values(), key=lambda r: r[1], reverse=True)[:limit]:
        row.pop("relevance", None)
        row["score"] = round(score, 6)
        row["snippet"] = row.pop("content")
        row["source_url"] = f"/api/workspace/courses/{course_id}/documents/{row['document_id']}/file"
        answer.append(row)
    return answer



def requested_presentation_ids(query: str, materials: list[dict]) -> list[str]:
    """Resolve a named deck without confusing `Ch 3` with `Chapter 02`.

    Matching is deliberately conservative: ambiguous requests must not silently
    introduce a different lecture into a whole-deck explanation.
    """
    def chapter_number(value: str) -> str | None:
        match = re.search(r"\b(?:chapter|ch|lecture|unit|module)\s*[-_. ]*0*(\d+)\b", value, re.I)
        return str(int(match.group(1))) if match else None

    pptx = [m for m in materials if m["extension"] == ".pptx" and m["status"] == "ready"]
    requested_number = chapter_number(query)
    if requested_number is not None:
        matched = [item["id"] for item in pptx if chapter_number(item["filename"]) == requested_number]
        if matched:
            return matched
    # Full stem, not scattered words such as "chapter"/"ppt".
    normalized_query = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    matches = []
    for item in pptx:
        stem = Path(item["filename"]).stem.lower()
        normalized_stem = re.sub(r"[^a-z0-9]+", " ", stem).strip()
        if len(normalized_stem) >= 5 and re.search(r"(?<!\w)" + re.escape(normalized_stem) + r"(?!\w)", normalized_query):
            matches.append(item["id"])
    return matches


def requested_document_ids(query: str, materials: list[dict]) -> list[str]:
    """Conservative name matching across PPTX/PDF/DOCX/MD/TXT."""
    matched = requested_presentation_ids(query, materials)
    if matched:
        return matched
    numeric = re.search(r"\b(?:chapter|ch|lecture|unit|module)\s*[-_. ]*0*(\d+)\b", query, re.I)
    normalized = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    results = []
    for item in materials:
        if item["status"] != "ready" or item["id"] in matched:
            continue
        stem = re.sub(r"[^a-z0-9]+", " ", Path(item["filename"]).stem.lower()).strip()
        number = re.search(r"\b(?:chapter|ch|lecture|unit|module)\s*0*(\d+)\b", stem)
        if ((len(stem) >= 5 and re.search(r"(?<!\w)" + re.escape(stem) + r"(?!\w)", normalized))
                or numeric and number and int(numeric.group(1)) == int(number.group(1))):
            results.append(item["id"])
    return results


def ordered_document_coverage(course_id: str, document_ids: list[str], *,
                              max_sources: int = 130, max_chars: int = 90_000) -> tuple[list[dict], list[dict]]:
    """Read all indexed units in document order for whole-document requests.

    Never substitute top-k vector search for full-document reading. If limited,
    distribute the budget and show exact coverage counts to the model/user.
    """
    from backend.app import database
    if not document_ids:
        return [], []
    if len(document_ids) > 30 or max_sources <= 0 or max_chars <= 0:
        raise ValueError("Invalid document coverage request")
    placeholders = ",".join("?" for _ in document_ids)
    with database() as conn:
        records = {r["id"]: dict(r) for r in conn.execute(f"""
            SELECT id,filename,extension,status,pages,chunk_count,warning
            FROM documents WHERE course_id=? AND status='ready' AND id IN ({placeholders})
        """, (course_id, *document_ids))}
        decks = [records[item] for item in document_ids if item in records]
        rows = [dict(r) for r in conn.execute(f"""
            SELECT c.id AS chunk_id,c.document_id,d.filename,d.extension,c.page,c.section,
                   c.content,c.ordinal FROM document_chunks c JOIN documents d ON d.id=c.document_id
            WHERE c.course_id=? AND d.status='ready' AND c.document_id IN ({placeholders})
            ORDER BY c.document_id,c.ordinal
        """, (course_id, *document_ids))]
        unit_coverage = {item: [dict(r) for r in conn.execute("""
            SELECT page,has_native,has_vision,indexed,warning FROM document_units
            WHERE document_id=? ORDER BY ordinal
        """, (item,))] for item in document_ids}
    groups: dict[str, list[dict]] = {doc["id"]: [] for doc in decks}
    for row in rows:
        if row["document_id"] in groups:
            groups[row["document_id"]].append(row)
    # First guarantee one chunk from each page/section when possible.
    by_page = []
    for doc in decks:
        subgroups = {}
        for row in groups[doc["id"]]:
            key = (row["page"], row["section"]) if row["page"] is not None else (row["ordinal"],)
            subgroups.setdefault(key, []).append(row)
        by_page.extend(list(subgroups.values()))
    first = [group[0] for group in by_page]
    if len(first) > max_sources:
        indices = [round(index * (len(first)-1) / (max_sources-1)) for index in range(max_sources)] if max_sources>1 else [0]
        picked = [first[index] for index in dict.fromkeys(indices)]
    else:
        picked = first
        for group in by_page:
            picked.extend(group[1: max_sources-len(picked)+1])
            if len(picked)>=max_sources:
                break
    rank = {doc["id"]: i for i,doc in enumerate(decks)}
    picked.sort(key=lambda r: (rank[r["document_id"]],r["ordinal"]))
    per_source = max(1,max_chars // max(1,len(picked)))
    excerpts = []
    for row in picked:
        snippet = row["content"][:per_source]
        excerpts.append({"chunk_id":row["chunk_id"],"document_id":row["document_id"],
                         "filename":row["filename"],"extension":row["extension"],
                         "page":row["page"],"section":row["section"],"snippet":snippet,
                         "excerpt_truncated":len(snippet)<len(row["content"]), "score":0.0,
                         "source_url":f"/api/workspace/courses/{course_id}/documents/{row['document_id']}/file"})
    summary = []
    for doc in decks:
        samples = [src for src in excerpts if src["document_id"]==doc["id"]]
        units = unit_coverage.get(doc["id"], [])
        summary.append({"filename":doc["filename"], "total_slides": doc["pages"] if doc["extension"]==".pptx" else None,
                        "total_pages": doc["pages"] if doc["extension"]==".pdf" else None,
                        "slides_with_indexed_text":sum(bool(item["indexed"]) for item in units),
                        "slides_in_context":len({src["page"] for src in samples}),
                        "units_processed":len(units), "units_visually_analyzed":sum(bool(item["has_vision"]) for item in units),
                        "units_without_readable_text":sum(not bool(item["indexed"]) for item in units),
                        "passages_indexed":doc["chunk_count"],"passages_in_context":len(samples),
                        "excerpt_truncated":any(src["excerpt_truncated"] for src in samples),
                        "warning":doc["warning"]})
    return excerpts, summary


def sample_course_sources(course_id: str, limit: int = 6,
                          document_ids: list[str] | None = None) -> list[dict]:
    """Give an assessment planner a bounded overview of indexed course content.

    Unlike keyword retrieval this intentionally does not require a topical query.
    Pick one passage per file before returning further passages, preventing a
    generic request like "make a question paper" from seeing zero evidence or
    sampling just the first uploaded deck. Explicit file scopes stay enforced.
    """
    from backend.app import database
    if limit <= 0 or (document_ids is not None and not document_ids):
        return []
    filter_sql = (" AND c.document_id IN (" + ",".join("?" for _ in document_ids) + ")") if document_ids else ""
    with database() as conn:
        rows = conn.execute(f"""
            SELECT * FROM (
                SELECT c.id AS chunk_id, c.document_id, d.filename, d.extension,
                       c.page, c.section, c.content, d.created_at,
                       ROW_NUMBER() OVER (PARTITION BY c.document_id ORDER BY c.ordinal) AS ordinal_rank
                FROM document_chunks c
                JOIN documents d ON d.id=c.document_id
                WHERE c.course_id=? AND d.status='ready' {filter_sql}
            ) WHERE ordinal_rank<=?
            ORDER BY created_at ASC, document_id ASC, ordinal_rank ASC
        """, (course_id, *(document_ids or []), limit)).fetchall()

    by_document: dict[str, list[dict]] = {}
    for row in rows:
        item = dict(row)
        item.pop("created_at", None)
        item.pop("ordinal_rank", None)
        by_document.setdefault(item["document_id"], []).append(item)
    selected: list[dict] = []
    while by_document and len(selected) < limit:
        for document_id in list(by_document):
            item = by_document[document_id].pop(0)
            item["score"] = 0.0  # overview sample, not a search relevance score
            item["snippet"] = item.pop("content")
            item["source_url"] = f"/api/workspace/courses/{course_id}/documents/{document_id}/file"
            selected.append(item)
            if not by_document[document_id]:
                del by_document[document_id]
            if len(selected) >= limit:
                break
    return selected


def source_for_chunk(conn: sqlite3.Connection, chunk_id: str) -> dict | None:
    row = conn.execute("""
        SELECT c.id AS chunk_id, c.document_id, d.course_id, d.filename, d.extension, c.page, c.section, c.content
        FROM document_chunks c JOIN documents d ON d.id=c.document_id
        WHERE c.id=? AND d.status='ready'
    """, (chunk_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["snippet"] = result.pop("content")
    result["source_url"] = f"/api/workspace/courses/{result.pop('course_id')}/documents/{result['document_id']}/file"
    return result
