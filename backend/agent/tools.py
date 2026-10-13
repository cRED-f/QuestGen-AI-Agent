"""Read-only academic investigation and an actual resumable professor prompt."""
import json


def build_tools(course_id: str, document_ids: list[str], *, thread_id: str = "", model=None):
    from langchain_core.tools import tool
    from langgraph.types import interrupt
    from backend import course_tools, documents
    from backend.agent import academic, consultation, research_tools, workflows

    @tool('search_course_passages')
    def search_course_passages(query: str) -> str:
        """Find grounded, relevant excerpts in the permitted course files for an academic concept."""
        if not document_ids:
            return '[]'
        results = documents.retrieve(course_id, query[:500], limit=8, document_ids=document_ids)
        return json.dumps(results, ensure_ascii=False, default=str)[:16000]

    @tool('inspect_topic_coverage')
    def inspect_topic_coverage(topic: str) -> str:
        """Inspect source coverage for a topic before making an academic recommendation."""
        if not document_ids:
            return json.dumps({'topic': topic, 'sources': [], 'notice': 'No ready course materials selected'})
        results = documents.retrieve(course_id, topic[:300], limit=12, document_ids=document_ids)
        return json.dumps({'topic': topic, 'sources': [
            {'filename': r.get('filename'), 'page': r.get('page'), 'section': r.get('section'),
             'excerpt': str(r.get('content', r.get('snippet', '')))[:750]}
            for r in results]}, ensure_ascii=False)

    @tool('analyze_course_topics')
    async def analyze_course_topics(batch_size: int = 14) -> str:
        """Build a cited concept graph from previously unread course passages.

        Processes up to 14 source chunks per call, rotating between selected files.
        Repeated calls continue across documents until coverage.complete is true.
        Includes subtopics, objectives, relations, opportunities and cognitive demand.
        Must use this before consulting the professor about particular topics.
        """
        if not thread_id or model is None:
            return json.dumps({'error': 'Academic analysis requires an active agent session'})
        report = await academic.analyze(thread_id, course_id, document_ids, model, batch_size=batch_size)
        return json.dumps(report, ensure_ascii=False, default=str)[:28000]

    @tool('get_academic_map')
    def get_academic_map() -> str:
        """Read saved evidence-cited topic graph, source coverage and earlier professor answers."""
        if not thread_id:
            return json.dumps({'error': 'No agent thread'})
        return json.dumps(academic.snapshot(thread_id, course_id, document_ids), ensure_ascii=False)[:28000]

    @tool('update_research_focus')
    def update_research_focus(professor_goal: str, topic_ids: list[str], rationale: str) -> str:
        """Update the persistent academic research plan using existing topic IDs and
        actual professor decisions. Call after a meaningful priority or scope change.
        Does not authorize question generation or change original course material.
        """
        if not thread_id:
            return json.dumps({'error': 'No agent thread'})
        return json.dumps(academic.set_research_focus(thread_id, course_id, document_ids,
                           goal=professor_goal, topic_ids=topic_ids, rationale=rationale), ensure_ascii=False)

    @tool('suggest_topic_consultation')
    async def suggest_topic_consultation(professor_goal: str) -> str:
        """Reason over discovered topics and prior answers to decide whether a substantive
        professor question is useful. Returns a contextual question or a proposed default;
        if needs_input is true, follow it by calling ask_professor to actually pause.
        """
        if not thread_id or model is None:
            return json.dumps({'error': 'No active academic consultation'})
        result = await consultation.suggest(thread_id, course_id, document_ids, model, professor_goal)
        return json.dumps(result, ensure_ascii=False)

    @tool('infer_cognitive_demand')
    async def infer_cognitive_demand(professor_goal: str) -> str:
        """Infer topic-specific conceptual, application, numerical and analytical demands
        from source evidence and professor priorities, without asking for a difficulty label.
        """
        if not thread_id or model is None:
            return json.dumps({'error': 'No active academic consultation'})
        return json.dumps(await consultation.demand(thread_id, course_id, document_ids, model,
                                                      professor_goal), ensure_ascii=False)

    @tool('propose_academic_work')
    def propose_academic_work(kind: str, title: str, rationale: str,
                              sections: list[dict] | None = None,
                              exam_type: str = 'custom', content: str = '',
                              course_chunk_ids: list[str] | None = None,
                              web_source_ids: list[int] | None = None) -> str:
        """Submit a REVIEWABLE professor plan, never execute it.

        kind: exam, question_bank, topic_coverage, research_summary, assessment_review.
        For exam/question_bank, sections contain {topic_id,kind,count,marks_each}
        where topic_id MUST come from get_academic_map; use original topics,
        not external-topic suggestions. The professor explicitly approves the plan,
        separately approves its editable paper blueprint, and reviews questions.
        For other work, content is a substantive research report with verified
        course_chunk_ids and/or saved web_source_ids as provenance.
        Propose a replacement to revise a plan when the professor requests it.
        """
        if not thread_id:
            return json.dumps({'error': 'An agent conversation is required'})
        from backend.app import database
        try:
            with database() as conn:
                plan = workflows.propose(conn, thread_id, course_id, document_ids, {
                    'kind': kind, 'title': title, 'rationale': rationale,
                    'sections': sections or [], 'exam_type': exam_type,
                    'content': content, 'course_chunk_ids': course_chunk_ids or [],
                    'web_source_ids': web_source_ids or []})
            return json.dumps({'proposal_id': plan['id'], 'status': 'proposed',
                'instruction': 'Waiting for professor review in the Agent workspace. Do not call this approved.'})
        except Exception as exc:
            # Validation errors must be visible to the model so it can repair its
            # proposal, but must not silently fabricate professor approval.
            return json.dumps({'error': str(exc)[:500]})

    @tool('ask_professor')
    def ask_professor(question: str, context: str = '', topic_ids: list[str] | None = None,
                      options: list[str] | None = None) -> str:
        """Pause for a meaningful professor decision grounded in actual course topics.

        Call only for consequential unresolved choices. The response is durably
        recorded against the topic graph after the same interrupted run resumes.
        """
        if not question.strip():
            return 'Provide a specific question before asking the professor.'
        allowed_ids = set()
        if thread_id:
            view = academic.snapshot(thread_id, course_id, document_ids)
            allowed_ids = {t['id'] for t in view['topics']}
        selected = [ident for ident in (topic_ids or [])[:8] if ident in allowed_ids]
        suggestions = [x.strip()[:140] for x in (options or [])[:5] if isinstance(x, str) and x.strip()]
        answer = interrupt({'type': 'professor_question', 'question': question[:1200],
                            'context': context[:800], 'topic_ids': selected,
                            'options': suggestions})
        if thread_id:
            academic.record_answer(thread_id, course_id, document_ids,
                                   question=question, answer=str(answer), context=context,
                                   topic_ids=selected)
        return str(answer)

    return [*course_tools.agent_tools(course_id, document_ids), search_course_passages,
            inspect_topic_coverage, analyze_course_topics, get_academic_map, update_research_focus,
            suggest_topic_consultation, infer_cognitive_demand, propose_academic_work, ask_professor,
            *research_tools.build_tools(thread_id, course_id, document_ids)]
