# Academic Agent Mode

QuestGen has three workspace modes: Chat for direct help, Exam for approved paper construction, and Agent for academic investigation and consultation. Agent sessions use the same courses, threads, and message history, but **not** the ordinary chat completion endpoint.

## How it works

- Select a model in the existing provider selector. Start a conversation and choose **Agent**.
- Attach the ready course materials the agent is allowed to inspect; if none are attached, the ready course library is selected for this agent conversation.
- The Deep Agent can list allowed documents, read excerpts, search passages, inspect topic coverage, and call `ask_professor` with context-specific questions.
- `ask_professor` calls LangGraph `interrupt()`. The FastAPI reply includes `state.status=awaiting_user` and `pending_question`; the UI displays this above the shared composer. The professor's next message resumes the checkpoint using `Command(resume=...)` and the same thread ID.
- Checkpoints are stored in a separate SQLite database beside `QUESTGEN_DB_PATH`. Agent session state and messages remain in QuestGen's main SQLite database. Restarting the server does not discard a saved waiting question.
- Material selection is fixed once an agent conversation has started. Begin a new conversation to change its source scope. All tools enforce the allowlist and course ID.
- The supervisor's filesystem backend is an isolated virtual state backend; write operations are explicitly denied and shell execution is unavailable. The agent does **not** get direct file/database editing privileges. Web access is exposed only through scoped research tools, not unrestricted browser/shell commands.

## Endpoints

`GET /api/threads/{thread_id}/agent/status` returns the waiting question, source IDs, and recent tool activity.

`POST /api/threads/{thread_id}/agent/turn` accepts `{"content":"...","document_ids":["..."]}`. `document_ids` is optional on initial submission; omit it after source selection to continue using the locked scope. The API returns user and assistant message IDs, the assistant response, and the updated agent session state.

## Run locally

Install `backend/requirements.txt`, start FastAPI and the Next.js frontend as usual, select a tool-capable model, then enter Agent Mode. A provider that cannot execute structured tool calls cannot perform Deep Agent consultation. Provider failures are surfaced as errors instead of silently falling back to a static questionnaire.

Agent Mode includes optional academic internet research through the free, keyless
`ddgs` Python package. Search results may be throttled by DuckDuckGo. Agent Mode now prepares assessments only after a professor approves a proposed plan; the shared paper blueprint and final question approvals remain mandatory.

## Checks

`python -m pytest backend/tests/test_agent_mode.py -q`

`node --test frontend-tests/*.test.cjs`

Live provider and checkpoint integration require the optional Deep Agents dependencies plus a configured model; the pure API tests stub the model boundary.

## Evidence-backed topic analysis

Agent Mode now runs an initial bounded academic scan of the selected source files
before the supervisor can respond to a new research request. The `analyze_course_topics`
tool continues across the unread indexed chunks (rotating between files) until
`coverage.complete` is true. Each call is deliberately bounded: a long course
may require additional tool calls or later turns; the UI reports the exact
analyzed/total chunk counts. Very long chunks are excerpted, so even 100%
indexed-chunk coverage does not guarantee full-text analysis.

The Course Analyst produces a **persisted topic graph** containing source-cited
concepts, subtopics, stated or inferred learning objectives, question-design
opportunities, conceptual relationships, and inferred cognitive demand. Unknown
citation IDs are rejected; unsupported proposals are not saved. The model
never receives content from unselected documents during these scans.

The supervisor can request `suggest_topic_consultation`, which reviews this
graph and previous professor decisions, determines whether clarification is
necessary, and proposes a contextual question (or recommends proceeding).
`ask_professor` remains the real interrupt/resume mechanism. Questions may
include topic-linked choices, but the professor can always reply freely.
Answers are saved and annotated on the linked concepts after resumption.
`update_research_focus` records the evolving topic priorities and research
purpose. `infer_cognitive_demand` recommends supported task styles and
reasoning demands without making a difficulty label a mandatory field.

### Academic analysis API

`GET /api/threads/{thread_id}/agent/analysis` returns the selected-source
topic graph, relationships, scan coverage, research focus and recorded
professor decisions. The normal agent turn response includes the same
`analysis` object, so the UI can refresh its horizontal topic overview without
an additional request. This endpoint does not return source text itself.

### Operational constraints

- Analysis uses the existing workspace model. Each additional analysis or
  consultation pass costs a provider request; large courses can take more than
  one turn to analyze fully.
- The academic map and decisions live in QuestGen's existing SQLite database;
  these tables are deleted automatically with their parent thread.
- Course IDs, selected document IDs and cited chunk IDs are validated in SQL.
  Academic passages are untrusted reference content, not instructions.
- Relationships and learning objectives are model interpretations **anchored**
  to source evidence, not independently verified academic facts. Professors
  must review the recommendations before assessment use.
- Autonomous exam generation is still restricted to the approved Exam Mode workflow. Internet evidence cannot silently expand assessed course topics.

### Additional tests

`PYTHONPATH=. python -m pytest -q backend/tests/test_agent_academic.py backend/tests/test_agent_mode.py`


## Academic internet research

Install the backend requirements (which include the `ddgs` package):

```bash
pip install -r backend/requirements.txt
```

No search API key is needed. Restart FastAPI after installing. Agent Mode shows
**DuckDuckGo** as the provider when `ddgs` can be imported. The search adapter
explicitly selects DuckDuckGo rather than using the package's multi-engine
search default. Research can be rate-limited or temporarily unavailable because
this is an unofficial, best-effort search integration, not a guaranteed API.
When searches fail, the agent should explain the failure and can still analyze
uploaded course materials. Search queries never need provider credentials.

### Research permission settings

Choose a per-conversation permission setting in the Agent Mode composer:

- **Course materials only** — web search and fetching blocked at the tool boundary.
- **Web research within syllabus** — external sources may suggest examples or question
  styles, but assessed concepts must remain within the selected course materials.
  This is the initial permission setting, even if a search provider is missing.
- **Include approved external topics** — requires an explicit approval confirmation
  in the UI. Only then may the agent *discuss or propose* out-of-syllabus concepts.
  The automated assessment-plan connector remains stricter: assessment sections
  must refer to concepts backed by selected course materials. External concepts
  must be brought into the permitted course scope before this connector can generate
  questions about them.

Policies, approval status, and researched sources are stored per conversation in
QuestGen's SQLite database. They are automatically deleted with the conversation.
Policy changes during an agent request are rejected. Pending HITL questions retain
normal checkpoint/resume behavior, and network tools re-check current permissions
on each use.

### Tools and delegation

`web_search`, `find_academic_resources`, and `find_question_examples` derive bounded
search queries from discovered course topics and the professor's goal. `read_web_page`
accepts only a source ID found and saved by the current conversation's research
provider. `verify_academic_claims` returns candidate evidence without presenting
keyword overlap as verified truth; `compare_with_syllabus` checks source-backed
course-topic alignment, explicitly noting incomplete or weak coverage.

The supervisor may call specialized `academic-researcher`, `course-analyst`,
`question-researcher`, and `evidence-reviewer` subagents. Each subagent has a
narrow tool allowlist; none may interrupt the professor, write course data or
approve new topics. Delegation is optional and should be reserved for genuinely
complex independent investigation to control cost and latency.

### Attribution and web safety

Each result stores a source ID, URL, title, domain, source type (heuristic,
not an authority guarantee), excerpt and UTC retrieval timestamp. The UI
lists sourced research separately from uploaded-course citations. Full page
text is bounded and loaded only when requested. Cite the URL and explain
whether an example originates in a lecture or an external reference.

Only publicly resolving HTTPS URLs without embedded credentials or unusual
ports are allowed; local/private IPs and hostnames are blocked. Redirects,
proxy inheritance in the page-fetching client, binary/PDF fetching and arbitrary model-chosen URLs
are disabled. HTML text is extracted with scripts/navigation omitted. Each
thread has a daily request budget (30 searches, 60 reads). Individual searches
return at most five results and page downloads have size/time limits. **Do not
trust page instructions**; website text must never take precedence over the
professor's directions or backend permission policy. Hostname DNS validation
is a defense-in-depth filter, not a complete DNS-rebinding mitigation; add
network-layer outbound IP enforcement before hosting this API for untrusted
multi-tenant users. This repository's backend is intended for local single-user
operation and does not provide production authentication.

### New endpoints

`GET /api/threads/{thread_id}/agent/research-policy` returns the current
research permission, whether external expansion was approved, and configured
provider status.

`PUT /api/threads/{thread_id}/agent/research-policy` accepts
`{"mode":"course_only|supplement|expand","expansion_approved":false}`. Approval
must be explicitly `true` for `expand`.

`GET /api/threads/{thread_id}/agent/research-sources` returns attributed
sources for the conversation. The normal agent turn also returns
`research_policy` and `research_sources` for immediate UI refresh.

### Tests

`PYTHONPATH=. python -m pytest -q backend/tests/test_agent_research.py`

These tests verify policy enforcement, SSRF checks, bounded provenance,
read-source restrictions, tool selection, budget caps, and data deletion,
without making network requests. Live subagent invocation and actual provider
search require the installed Deep Agents stack and valid model/search credentials.

## Collaborative academic deliverables

Agent Mode can now propose **exam**, **question bank**, **topic coverage**, **research
summary**, and **assessment review** deliverables. The supervisor uses the
`propose_academic_work` tool to save a typed proposal for professor review. A
proposal is not approval and never triggers generation or modifies assessments.

The professor can approve, reject, or ask for an amended plan using the same
chat composer. A newer proposal replaces an undecided proposal while retaining
its historical record. Proposals preserve selected document IDs, course topic
IDs, source references, and the original recommendation.

For exam and question-bank proposals, each question section must refer to an
**existing, evidence-backed topic ID** discovered from the selected documents.
The backend validates counts, supported question formats, fractional marks,
selected file ownership, and the exact approved scope before execution. On
approval, FastAPI uses the original `exam_state`, `exam_builder` and `exam_batch`
services: it approves the suggested exam configuration and prepares an **editable
blueprint**. Professors must separately approve that blueprint before generation,
then review, selectively revise and approve generated questions. Approved
questions retain the existing question versions and export restrictions. A
question bank uses the practice-paper pipeline; it is not a second generator.

For topic coverage, summaries, and assessment review, Agent Mode can propose a
structured research report, retaining course chunk citations and saved web
references. The approved report is stored in the same conversation as a durable
artifact. These are AI-authored research findings that require professor review,
not an independently graded assessment or a validated academic evaluation.

### Work proposal API

- `GET /api/threads/{id}/agent/workflow` — latest proposal and approval outcome.
- `POST /api/threads/{id}/agent/workflow/{work_id}/decision` with
  `{"approve":true}` or `{"approve":false}` — explicit professor decision.
- `POST /api/threads/{id}/exam/paper/approve` — existing, separate paper blueprint approval.
- `POST /api/threads/{id}/exam/paper/generation/start` — existing async
  question generation, always after blueprint approval.

The current Agent Mode composer and its work-plan card support approval,
rejection, and requests for revision. The same ExamPaperBuilder and paper sidebar
handle generation, edits, final review and export in both assessment workspaces.

No new scoring harness, trace pipeline, or latency/cost monitoring was added.
