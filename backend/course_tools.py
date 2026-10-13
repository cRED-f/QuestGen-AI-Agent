"""Read-only course inspection tools for Deep Agents supervisors.

Agents may look, never write: file metadata and bounded ordered chunk reads,
always scoped to a single course. No execute, no network, no cross-course access.
"""
from __future__ import annotations

import json

from backend import documents


def _course_materials(course_id: str, document_ids: list[str] | None = None) -> list[dict]:
    from backend.app import database
    with database() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT id,filename,extension,status,chunk_count,pages,warning FROM documents "
            "WHERE course_id=? ORDER BY created_at ASC", (course_id,))]
        return [row for row in rows if document_ids is None or row["id"] in document_ids]


def list_course_documents(course_id: str, document_ids: list[str] | None = None) -> list[dict]:
    """Every course file with scale metadata; contents are never included."""
    return _course_materials(course_id, document_ids)


def read_document_chunks(course_id: str, filename_query: str, *, max_units: int = 12,
                         max_chars: int = 8000, document_ids: list[str] | None = None) -> list[dict]:
    """Ordered excerpts from the named file; 'Ch 3' style names resolve too."""
    materials = [item for item in _course_materials(course_id, document_ids) if item['status'] == 'ready']
    ids = documents.requested_document_ids(filename_query, materials)
    if not ids:
        return []
    sources, _coverage = documents.ordered_document_coverage(
        course_id, ids[:1], max_sources=max(1, min(max_units, 40)), max_chars=max_chars)
    return sources


def agent_tools(course_id: str, document_ids: list[str] | None = None):
    """Two @tool wrappers bound to one course, for create_deep_agent(...)."""
    from langchain_core.tools import tool

    @tool("list_course_documents")
    def list_course_documents_tool() -> str:
        """List this course's uploaded files: name, status, pages, passage counts, warnings."""
        return json.dumps(_course_materials(course_id, document_ids), ensure_ascii=False)

    @tool("read_document_chunks")
    def read_document_chunks_tool(filename_query: str) -> str:
        """Read ordered excerpts from the file whose name best matches filename_query (e.g. 'Ch 3')."""
        return json.dumps(read_document_chunks(course_id, filename_query, document_ids=document_ids), ensure_ascii=False)

    return [list_course_documents_tool, read_document_chunks_tool]
