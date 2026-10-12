"""Bounded, course-scoped evidence and per-slot complexity plan for whole-paper drafts.

The professor's approved blueprint is the source of authority. Untrusted material
is data, never instructions; only approved document IDs may enter this package.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict

from backend import documents, exam_builder

BLOOM = ('remember', 'understand', 'apply', 'analyze', 'evaluate', 'create')


def complexity(slot: dict, exam_type: str) -> tuple[str, str]:
    """A transparent baseline when no level is specified, not an accuracy claim.

    The writer sees the materials and the target level, which is checked again by
    the professor. No demographic/course level is guessed from unrelated metadata.
    """
    if slot['difficulty'] != 'auto':
        value = slot['difficulty']
        bloom = ('understand' if value == 'easy' else 'apply' if value == 'moderate'
                 else 'analyze' if value == 'complex' else 'evaluate')
        return value, bloom
    kind, marks = slot['kind'], slot['marks']
    if kind == 'problem_solving':
        return ('complex', 'analyze') if marks >= 5 else ('moderate', 'apply')
    if kind in ('long_answer', 'custom'):
        return ('complex', 'evaluate') if marks >= 8 else ('moderate', 'analyze')
    # Rotate complexity within a section instead of making all 20 identical;
    # question type, marks and purpose influence the distribution.
    index = slot['section_number'] - 1
    sequence = ('easy', 'moderate', 'moderate', 'complex') if exam_type in ('midterm', 'final') else (
        'easy', 'moderate', 'easy', 'complex')
    level = sequence[index % len(sequence)]
    return level, {'easy': 'understand', 'moderate': 'apply', 'complex': 'analyze'}[level]


def _topic(source: dict, fallback: str) -> str:
    name = str(source.get('section') or '').strip()
    if not name or len(name) < 3:
        name = str(source.get('filename') or fallback).rsplit('.', 1)[0]
    return name[:160] or fallback


async def build(session: dict, blueprint: dict, material_ids: list[str]) -> dict:
    """Retrieve once per section (not once per question), off the event loop."""
    slots = exam_builder._slots(exam_builder.Blueprint.model_validate(blueprint))
    section_sources: dict[str, list[dict]] = {}
    scope = session['requirements']['material_scope']
    if scope != 'general' and not material_ids:
        raise ValueError('No approved course materials available')
    for section in blueprint['sections']:
        if scope == 'general':
            found = []
        else:
            found = await asyncio.to_thread(
                documents.retrieve, session['course_id'], section['topic'], 12, material_ids)
            if not found:
                found = await asyncio.to_thread(
                    documents.sample_course_sources, session['course_id'], 12, material_ids)
            if not found:
                raise ValueError(f'No indexed evidence for section {section["id"]}. Reprocess the course files.')
            # No source outside the approved set can enter the generation prompt.
            if any(s.get('document_id') and s['document_id'] not in material_ids for s in found):
                raise ValueError('Retrieval returned an unapproved document')
        section_sources[section['id']] = found
    planned = []
    for slot in slots:
        found = section_sources[slot['section_id']]
        level, bloom = complexity(slot, session['requirements'].get('exam_type', 'custom'))
        # Different question positions rotate through chapter evidence, rather
        # than presenting one repetitive excerpt to the model for every slot.
        offset = (slot['section_number'] - 1) % len(found) if found else 0
        chosen = (found[offset:] + found[:offset])[:3]
        planned.append({**slot, 'topic': _topic(chosen[0], slot['topic']) if chosen else slot['topic'],
                        'difficulty': level, 'bloom_level': bloom,
                        'evidence': [{'chunk_id': s['chunk_id'], 'snippet': str(s.get('snippet') or '')[:1100],
                                      'filename': s.get('filename', ''), 'section': s.get('section', '')}
                                     for s in chosen]})
    return {'slots': planned, 'blueprint': blueprint, 'material_ids': sorted(material_ids),
            'scope': scope, 'blueprint_digest': hashlib.sha256(
                json.dumps(blueprint, sort_keys=True).encode()).hexdigest()}
