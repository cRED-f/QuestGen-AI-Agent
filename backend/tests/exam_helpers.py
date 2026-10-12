"""Shared regression helper: explicitly approve the approved exam before invoking the legacy generation worker."""

def approve_exam(client, thread_id, *, document_ids=None, total_marks=10):
    root = f"/api/threads/{thread_id}/exam"
    assert client.post(root + "/start").status_code == 200
    updates = {
        "faculty_initials": "FI",
        "university_name": "Test University",
        "exam_type": "quiz",
        "total_marks": total_marks,
        "difficulty": "moderate",
        "question_formats": [{"kind": "short_answer", "count": 1, "marks_each": total_marks}],
        "material_scope": "selected" if document_ids else "all_course",
    }
    payload = {"updates": updates}
    if document_ids:
        payload["document_ids"] = document_ids
    saved = client.patch(root + "/requirements", json=payload)
    assert saved.status_code == 200, saved.text
    approved = client.post(root + "/approve")
    assert approved.status_code == 200, approved.text
