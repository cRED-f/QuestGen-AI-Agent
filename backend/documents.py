"""Durable local document ingestion, indexing and course-scoped retrieval.

This is an unauthenticated *single-user* application. Course filters provide logical
workspace separation, not an authorization boundary.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import httpx
from docx import Document as WordDocument
from pypdf import PdfReader

MAX_UPLOAD = 15 * 1024 * 1024
MAX_EXTRACTED = 700_000
MAX_PAGES = 300
MAX_CHUNKS = 600
ALLOWED = {".pdf", ".docx", ".txt", ".md"}
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


def document_path(document_id: str, extension: str) -> Path:
    return uploads_root() / (document_id + extension)


def validate_payload(filename: str, payload: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED:
        raise ValueError("Supported documents: PDF, DOCX, TXT, MD")
    if not payload or len(payload) > MAX_UPLOAD:
        raise ValueError("File must be between 1 byte and 15 MiB")
    if extension == ".pdf" and not payload.startswith(b"%PDF-"):
        raise ValueError("File is not a valid PDF")
    if extension == ".docx":
        if not payload.startswith(b"PK"):
            raise ValueError("File is not a valid DOCX")
        try:
            from io import BytesIO
            with ZipFile(BytesIO(payload)) as archive:
                items = archive.infolist()
                if len(items) > 2500 or sum(x.file_size for x in items) > 100 * 1024 * 1024:
                    raise ValueError("DOCX archive exceeds extraction limits")
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("File is not a valid DOCX")
        except BadZipFile as exc:
            raise ValueError("File is not a valid DOCX") from exc
    if extension in (".txt", ".md"):
        try:
            payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Text uploads must be UTF-8") from exc
    return extension


def extract_segments(path: Path, extension: str) -> list[tuple[int | None, str | None, str]]:
    """Each segment is a page/section with its own independently verifiable locator."""
    segments: list[tuple[int | None, str | None, str]] = []
    size = 0

    def append(page: int | None, section: str | None, text: str) -> None:
        nonlocal size
        text = re.sub(r"[ \t]+", " ", text).strip()
        if not text:
            return
        size += len(text)
        if size > MAX_EXTRACTED:
            raise ValueError("Extracted text exceeds the 700,000-character limit")
        segments.append((page, section, text))

    if extension == ".pdf":
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("PDF exceeds the 300-page limit")
        for number, page in enumerate(reader.pages, 1):
            append(number, None, page.extract_text() or "")
        if not segments:
            raise ValueError("No extractable PDF text. Scanned PDFs require OCR (not enabled).")
    elif extension == ".docx":
        document = WordDocument(str(path))
        section = None
        blocks: list[str] = []
        for paragraph in document.paragraphs:
            content = paragraph.text.strip()
            if not content:
                continue
            if paragraph.style and paragraph.style.name.lower().startswith("heading"):
                if blocks:
                    append(None, section, "\n".join(blocks))
                    blocks = []
                section = content[:160]
            else:
                blocks.append(content)
        if blocks:
            append(None, section, "\n".join(blocks))
        for table in document.tables:
            rows = [" | ".join(cell.text for cell in row.cells) for row in table.rows]
            append(None, "Table", "\n".join(rows))
    else:
        append(None, None, path.read_text(encoding="utf-8-sig"))
    if not segments:
        raise ValueError("Document contains no readable text")
    return segments


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
    url = os.getenv("QUESTGEN_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/") + "/api/embed"
    vectors: list[list[float]] = []
    with httpx.Client(timeout=httpx.Timeout(90, connect=5)) as client:
        for start in range(0, len(texts), 16):
            result = client.post(url, json={"model": model, "input": texts[start:start + 16]})
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
        segments = extract_segments(path, row["extension"])
        set_progress(doc_id, "chunking", 55)
        chunks = split_segments(segments)
        set_progress(doc_id, "indexing", 75)
        warning = None
        vectors: list[list[float]] = []
        if os.getenv("QUESTGEN_EMBED_MODEL", "").strip():
            try:
                vectors = embed_texts([chunk["content"] for chunk in chunks])
            except (httpx.HTTPError, ValueError) as exc:
                warning = "Optional Ollama embeddings unavailable; keyword search remains active."
        with database() as conn:
            if conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone() is None:
                return  # deleted while indexing
            old_ids = [r[0] for r in conn.execute("SELECT rowid FROM document_chunks WHERE document_id=?", (doc_id,))]
            for rowid in old_ids:
                conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (rowid,))
            conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
            for ordinal, chunk in enumerate(chunks):
                chunk_id = hashlib.sha256(f"{doc_id}:{ordinal}".encode()).hexdigest()[:32]
                cur = conn.execute(
                    "INSERT INTO document_chunks(id,document_id,course_id,page,section,ordinal,content,embedding) VALUES (?,?,?,?,?,?,?,?)",
                    (chunk_id, doc_id, row["course_id"], chunk["page"], chunk["section"], ordinal,
                     chunk["content"], json.dumps(vectors[ordinal]) if vectors else None),
                )
                conn.execute("INSERT INTO chunks_fts(rowid,content) VALUES (?,?)", (cur.lastrowid, chunk["content"]))
            conn.execute("UPDATE documents SET status='ready', progress=100, error=NULL, warning=?, "
                         "pages=?,chunk_count=?,updated_at=? WHERE id=?",
                         (warning, len(segments) if row["extension"] == ".pdf" else 0,
                          len(chunks), now(), doc_id))
    except Exception as exc:
        # Detailed errors are kept server-side. No file path or embedded secrets leave the API.
        label = str(exc) if isinstance(exc, ValueError) else "Could not extract or index this document"
        set_progress(doc_id, "failed", 100, error=label[:200])


def retrieve(course_id: str, query: str, limit: int = 6) -> list[dict]:
    from backend.app import database
    terms = [word for word in TOKEN_RE.findall(query.lower()) if (len(word) >= 3 or word in {"ai", "ml", "db"}) and word not in STOPWORDS][:16]
    if not terms:
        return []
    matched: dict[str, tuple[dict, float]] = {}
    with database() as conn:
        expression = " OR ".join('"' + item.replace('"', '') + '"' for item in terms if len(item) >= 2)
        if expression:
            rows = conn.execute("""
                SELECT c.id AS chunk_id, c.document_id, d.filename, c.page, c.section,
                       c.content, bm25(chunks_fts) AS relevance
                FROM chunks_fts JOIN document_chunks c ON c.rowid=chunks_fts.rowid
                JOIN documents d ON d.id=c.document_id
                WHERE chunks_fts MATCH ? AND c.course_id=? AND d.status='ready'
                ORDER BY relevance LIMIT 30
            """, (expression, course_id)).fetchall()
            for rank, row in enumerate(rows):
                matched[row["chunk_id"]] = (dict(row), 1.0 / (60 + rank + 1))
        if os.getenv("QUESTGEN_EMBED_MODEL", "").strip():
            try:
                queries = embed_texts([query])
                if queries:
                    q = queries[0]
                    ranked = []
                    for row in conn.execute("""
                        SELECT c.id AS chunk_id, c.document_id, d.filename, c.page, c.section,
                               c.content, c.embedding FROM document_chunks c
                        JOIN documents d ON d.id=c.document_id
                        WHERE c.course_id=? AND d.status='ready' AND c.embedding IS NOT NULL
                        LIMIT 5000
                    """, (course_id,)):
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


def source_for_chunk(conn: sqlite3.Connection, chunk_id: str) -> dict | None:
    row = conn.execute("""
        SELECT c.id AS chunk_id, c.document_id, d.course_id, d.filename, c.page, c.section, c.content
        FROM document_chunks c JOIN documents d ON d.id=c.document_id
        WHERE c.id=? AND d.status='ready'
    """, (chunk_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["snippet"] = result.pop("content")
    result["source_url"] = f"/api/workspace/courses/{result.pop('course_id')}/documents/{result['document_id']}/file"
    return result
