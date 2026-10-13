"""Validate a thread's course-scoped, immutable source selection."""
from fastapi import HTTPException


def selected_documents(conn, course_id: str, requested: list[str] | None) -> list[str]:
    rows = conn.execute("SELECT id FROM documents WHERE course_id=? AND status='ready' ORDER BY created_at",
                        (course_id,)).fetchall()
    permitted = {r['id'] for r in rows}
    if requested is None:
        return [r['id'] for r in rows]
    chosen = list(dict.fromkeys(requested))
    if len(chosen) != len(requested) or any(item not in permitted for item in chosen):
        raise HTTPException(status_code=422, detail='Selected materials must be ready and belong to this course')
    return chosen
