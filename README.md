<p align="center">
  <img src="app/icon.svg" alt="QuestGen pencil icon" width="88" height="88" />
</p>

<p align="center"><strong>QuestGen</p>

<p align="center">A chat-based assessment workspace for professors.</p>

QuestGen lets you organize course materials, discuss them with an AI assistant, draft an assessment, revise individual questions, review and approve the results, and export student or instructor copies.

> **Status:** Active development.

## Chat, Agent and Exam Mode

QuestGen has three modes in a shared course conversation. The active course, selected materials and saved thread remain available when you switch modes.

- **Chat Mode:** General conversations, course-material questions, citations and streamed responses.
- **Agent Mode:** A tool-using academic assistant that discusses your objectives, researches when necessary, drafts complete questions **in Agent Mode**, and lets you review and select them. You do not need a separate Web Search action or Exam Mode to create questions.
- **Exam Mode:** A paper-building workspace. Generate an assessment directly there, or import the questions you selected in Agent Mode, arrange the paper, approve it and export printable copies.

### Agent Mode: from discussion to questions

1. **Discuss the assessment.** Explain the topic or learning goal in chat. The agent asks context-dependent follow-up questions about scope, question types, coverage and marks rather than immediately generating a question set. It can infer reasonable complexity from the course materials; you do not have to provide a difficulty label.
2. **Review the consultation plan.** The compact **Your assessment plan** panel summarizes the proposed paper. Expand **Plan details** for the breakdown, or discuss changes in chat. Question generation is blocked until you explicitly choose **Approve plan & draft questions**. Revisions to the plan require fresh approval.
3. **Draft and review in Agent Mode.** After approval, the agent generates complete questions (including MCQs, short and long answers, problem-solving and other supported formats) and stores them in the thread's draft bank. The **Review your questions** tray shows a compact, scrollable list rather than taking over the chat window. Open one question at a time, edit its content, view or hide suggested answers, remove unwanted questions and select exactly which ones to keep. You can discuss further changes with the agent before finalizing your selection.
4. **Choose when to format.** Click **Format selected in Exam Mode** only when the selected drafts are ready. A QuestGen-styled confirmation dialog shows what will be transferred. If an Exam Mode paper already exists, the dialog switches to an explicit replacement warning. No new model generation is required for this handoff: selected question content is preserved and the conversation context stays with the thread.
5. **Finish in Exam Mode.** Review the organized sections, marks, paper title and instructions. The **Approve final paper** button opens a matching, themed confirmation dialog. Final approval enables student and instructor **PDF/DOCX** exports; editing the approved paper requires approval again.

The consultation panel, question tray and confirmation dialogs use QuestGen's paper-and-sage styling with mobile and dark-theme layouts. The draft tray is bounded to the chat workspace (up to 790px wide and 385px tall on desktop) and scrolls its question list internally. Both paper confirmation dialogs support keyboard focus handling, Escape to cancel and visible errors.

### Exam Mode: direct assessment workflow

Exam Mode also supports building a paper without an Agent Mode handoff. It uses adaptive clarification for missing essential requirements, mixed-question blueprints, and course-aware planning. A Deep Agents planner may infer topic coverage and complexity; if it cannot produce a supported plan, the application falls back to a deterministic one. Batch generation attempts all planned questions together and falls back to smaller groups if the result is too large or invalid. Individual drafts support **Keep, Edit, Revise and Approve kept**, with targeted regeneration that does not overwrite other drafts or accepted questions. The original single-question generation API remains available.

The exam-paper sidebar shows accepted questions, marks, progress, editing, reordering and removal. Export of the final paper is a separate professor-approved action. Generated academic content is still a draft until reviewed.

## Current features

- **Chat workspace:** streamed model replies, persisted conversations, message editing in the main composer, response regeneration, and direct conversation deletion.
- **Agent workspace:** SSE-streamed consultation and optional research, versioned plan approval, saved full-question drafts, compact expandable review/selection, message Edit/Regenerate, and selected-question handoff without regeneration.
- **Courses:** create and switch courses; each has its own chat history and material Library.
- **Material Library:** upload, browse, attach, search, open, delete, inspect extraction/vision coverage and reprocess files. Chat and assessment generation can retrieve from a selected subset of materials, or from the active course if none are selected.
- **Document extraction:** PDF, DOCX, PPTX, TXT, Markdown, PNG, JPG/JPEG, WEBP, BMP and TIFF. PowerPoint processing combines native slide text, tables, editable chart data and speaker notes with complete slide-image analysis using the selected multimodal model when slide rendering is available. PDF and DOCX use native-first, selective vision processing.
- **Retrieval and citations:** SQLite full-text search with optional Ollama embeddings, course filters, and page/slide references when available.
- **Model providers:** 9Router, OpenAI, Anthropic, OpenRouter and Ollama. Configure credentials/connection in Model Settings, then select an available model in chat.
- **Assessment drafts:** Deep Agents-based blueprint planning followed by bounded question generation, validation and review. SQLite stores the blueprint, completed questions, progress events and restart recovery state.
- **Question review:** edit or selectively regenerate a question, use conversational customization, lock/unlock, inspect versions, restore an older revision, and approve or reject individual questions.
- **Paper review and exports:** themed confirmation dialogs for Agent-to-Exam handoff and final approval; separate PDF/DOCX student and instructor copies. Student copies exclude answer keys, while instructor copies include answers and explanations. The final paper must be approved before interactive-paper export.

Generated questions are **drafts**. AI academic reviews do not replace a professor's judgment or establish factual correctness.

## Implementation overview

| Layer       | Current implementation                                                                                                                                                                               |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Interface   | Next.js 16.4.0, React 19.3.0, TypeScript, Tailwind CSS                                                                                                                                               |
| Backend     | FastAPI, Python, Pydantic                                                                                                                                                                            |
| AI          | Provider SSE/token and tool-call streaming; SSE Agent Mode runner (no fixed reasoning/tool-cycle cap); Deep Agents supervisor for assessment blueprints; separate structured generation/review calls |
| Persistence | Local SQLite (courses, messages, documents, assessments, question versions, provider settings)                                                                                                       |
| Retrieval   | SQLite FTS5; optional Ollama embeddings; citations to stored passages                                                                                                                                |
| Extraction  | Unified `backend/ingestion.py` dispatcher; PyMuPDF, python-docx, python-pptx, Pillow, selected-model image analysis (`backend/vision.py`)                                                            |
| Output      | ReportLab PDF and python-docx DOCX                                                                                                                                                                   |

Normal chat uses direct model streaming; Agent Mode runs a separate unbounded-by-application tool-calling loop (provider context/output and infrastructure constraints still apply); **not every chat turn runs Deep Agents**. Deep Agents is used for planning a structured assessment. Generation and review then use bounded model calls. Work is processed by local in-process workers, not a distributed task queue.

### Agent Mode execution and research

Agent Mode uses the model selected in **Model Settings**. The model must support streamed tool/function calls; a model appearing in the picker does **not** guarantee that capability. QuestGen starts the SSE response promptly and streams provider-exposed reasoning, tool activity and answer text as they arrive. SSE keepalives continue during longer model calls. Agent Mode has **no application-defined reasoning-step or tool-cycle limit** and no model-stream application read timeout; provider context/output limits, cancellations and deployment timeouts may still apply.

The model decides when it needs course evidence or public web information. Web research is automatic, not a separate UI action. Each agent turn allows at most **two web searches and three external HTTPS page reads** (including failed attempts); page reads may run concurrently. Search uses the public DuckDuckGo HTML index with Bing RSS fallback, or Brave Search when `QUESTGEN_BRAVE_SEARCH_API_KEY` is configured on the backend. Course retrieval is scoped to the active course and respects explicitly selected documents. Sources are stored with completed answers. Public deployment should additionally restrict outbound network access.

### Agent Mode API and persistence

- `POST /api/threads/{thread_id}/agent/stream` — streams `started`, `status`, `reasoning`, `activity`, `plan`, `sources`, `delta`, `done` and `error` SSE events, along with keepalives. Incomplete/error turns are not saved as successful responses.
- `GET /api/threads/{thread_id}/agent/plan` — gets the current versioned consultation plan.
- `POST /api/threads/{thread_id}/agent/plan/approve` — approves exactly the current plan `version`. The model can propose a plan but cannot approve it; drafting requires approval and an authorized `generate_from_plan` turn.
- `GET /api/threads/{thread_id}/agent/questions` — loads saved Agent Mode drafts.
- `PATCH /api/threads/{thread_id}/agent/questions/{question_id}` — edits question content or its selection state.
- `DELETE /api/threads/{thread_id}/agent/questions/{question_id}` — removes a question draft.
- `POST /api/threads/{thread_id}/exam/import-agent-questions` — transfers selected drafts into an Exam Mode paper, retaining the content and conversation snapshot without regenerating questions.
- `PUT /api/threads/{thread_id}/exam/paper/layout` — updates the imported paper's title/instructions; a changed paper must be approved again.

Agent Mode **Edit** replaces an earlier user prompt and subsequent messages before streaming a new turn. **Regenerate** retries an assistant response using the original prompt and selected course files; it does not duplicate the old response. Superseded sources, plans and drafts are invalidated as appropriate. A regeneration of a previously authorized drafting turn can reuse that authorization; an ordinary chat request cannot bypass the approval gate. The local single-process runtime supports one active Agent Mode response per thread.

Exam-paper replacement and final approval are explicit user confirmations, handled by the shared `PaperActionDialog` component (`components/workspace/PaperActionDialog.tsx`). The dialogs are styled and rendered within the app's theme rather than using native browser confirmation popups.

## Prerequisites

- Node.js **20.9 or newer**, plus npm.
- A supported Python 3 installation and pip (Python **3.11+ recommended**).
- An AI provider endpoint and an available model. For local models, install and start [Ollama](https://ollama.com/).
- **For PowerPoint vision:** Install LibreOffice on the backend machine. It renders full slides as PDFs before vision analysis. Choose an image-capable chat model; no separate Tesseract or Ollama vision configuration is used.

Docker, Kubernetes, Convex and sign-in accounts are **not required** for the current local workflow. Some old Convex/Next.js service code remains in the repository but is not the primary application backend.

## Install and run

Run commands from the repository root. The frontend and backend are separate processes.

### 1. Install Python requirements

**Windows PowerShell**:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
```

**macOS / Linux**:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
```

### 2. Run the backend

```bash
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

Confirm the backend responds at <http://127.0.0.1:8000/api/health>.

### 3. Install the frontend and run it

In another terminal:

```bash
npm install
npm run dev
```

Open <http://localhost:3000/chat>.

## Configure an AI provider

In QuestGen, open **Model Settings**, select a provider, configure the key and/or server address, and use the **Test connection** action. Then select an available model from the **chat model picker**. The backend stores configured credentials encrypted locally and does not send saved secret values back to the browser. Protect the database **and** its local encryption-key file when backing up or moving the application.

Environment variables are also supported (set them **before** starting FastAPI):

| Provider   | Variables                                                                                                          |
| ---------- | ------------------------------------------------------------------------------------------------------------------ |
| OpenAI     | `OPENAI_API_KEY`                                                                                                   |
| Anthropic  | `ANTHROPIC_API_KEY`                                                                                                |
| OpenRouter | `OPENROUTER_API_KEY`                                                                                               |
| 9Router    | `QUESTGEN_9ROUTER_URL` (default `http://127.0.0.1:20128/v1`); optional `NINE_ROUTER_API_KEY`                       |
| Ollama     | `QUESTGEN_OLLAMA_URL` (default `http://127.0.0.1:11434`); optional `QUESTGEN_OLLAMA_API_KEY` for protected proxies |

Anthropic generation currently uses an OpenAI-compatible API path, while model discovery uses its model-list endpoint. Availability in the picker **does not guarantee** that a model supports tool calling or structured assessment generation. Test the actual generation workflow with your chosen model.

#### Exam configuration API

- `POST /api/threads/{thread_id}/exam/start` — create or resume the examination setup.
- `GET /api/threads/{thread_id}/exam` — read the current configuration.
- `POST /api/threads/{thread_id}/exam/turn` — discuss and extract explicit requirements.
- `PATCH /api/threads/{thread_id}/exam/requirements` — save validated fields and document selection.
- `POST /api/threads/{thread_id}/exam/approve` — approve complete requirements.

#### Interactive paper API

- `GET /api/threads/{thread_id}/exam/paper` — restore the paper, candidate and accepted questions.
- `POST /api/threads/{thread_id}/exam/paper/generation/start` with `{ "mode": "batch" }` — generate all missing questions in one logical operation; model output-size fallback is automatic.
- Same endpoint with `{ "mode": "revision", "draft_ids": ["..."], "instructions": "Make these more analytical" }` — regenerate only drafts previously flagged for revision.
- `PUT /api/threads/{thread_id}/exam/paper/drafts/decision` — mark a draft Keep or Revise with a version check.
- `PATCH /api/threads/{thread_id}/exam/paper/drafts/{draft_id}` — edit a draft, preserving history.
- `POST /api/threads/{thread_id}/exam/paper/drafts/approve` — atomically accept selected kept drafts; other questions are untouched.
- `POST /api/threads/{thread_id}/exam/paper/prepare` — prepare a syllabus-aware section blueprint.
- `PUT /api/threads/{thread_id}/exam/paper/blueprint` — revise title, topic and difficulty without changing approved marks.
- `POST /api/threads/{thread_id}/exam/paper/approve` — approve the blueprint.
- `POST /api/threads/{thread_id}/exam/paper/next` — generate and review a candidate for the next unfilled slot.
- `POST /api/threads/{thread_id}/exam/paper/regenerate` — replace the candidate without saving it to the paper.
- `PATCH /api/threads/{thread_id}/exam/paper/candidate` — edit a pending candidate.
- `POST /api/threads/{thread_id}/exam/paper/accept` — accept one candidate into the paper.
- `POST /api/threads/{thread_id}/exam/paper/skip` — discard one candidate without using marks.
- `PATCH /api/threads/{thread_id}/exam/paper/questions/{question_id}` — edit a saved question.
- `DELETE /api/threads/{thread_id}/exam/paper/questions/{question_id}` — remove a question and reopen its slot.
- `PUT /api/threads/{thread_id}/exam/paper/order` — reorder accepted questions.
- `GET /api/threads/{thread_id}/exam/paper/questions/{question_id}/versions` — inspect saved edit history.

The existing bulk-generation endpoints remain available for older assessment jobs. The interactive paper has its own final-approval and PDF/DOCX export endpoints (`/api/threads/{thread_id}/exam/paper/final-approve` and `/api/threads/{thread_id}/exam/paper/export/{audience}.{format}`).

Run `PYTHONPATH=. pytest backend/tests -q` from the repository root to execute regression tests.

## Exam Mode human-in-the-loop

Requirements are collected one question at a time. When a chat message proposes a full
requirement set, the server keeps only fields the professor actually stated (deterministic
evidence gate), and a _completing_ extraction is never saved silently: QuestGen summarizes
it ("I heard: Midterm · 50 marks · 10 MCQ × 1…") and waits for an explicit yes. Approval
and question generation are always separate professor actions.

## Supervisor agent tools

The blueprint supervisor and exam planner run as LangChain deepagents with read-only tools:
custom `search_course_materials` / `inspect_slot_evidence`, plus `list_course_documents`
and `read_document_chunks` (course-scoped file listing and bounded ordered excerpts).
deepagents' virtual-FS tools (`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`)
are active by default inside the agent state; the `execute` tool, long-term memory, and
web access are deliberately not enabled.

## Unified document ingestion, vision, and embeddings

Document ingestion has **one** path: `backend/ingestion.py` combines trusted native-format parsing with optional **selected chat model** visual interpretation through `backend/vision.py`. Old `QUESTGEN_VISION_MODEL`, Tesseract OCR, and the embedded-picture-only PowerPoint extraction path have been removed. The model used for vision comes directly from **Model Settings** (including Ollama, OpenAI-compatible gateways, OpenRouter and native Anthropic Messages). The chosen model **must actually support image inputs**; merely being listed in model discovery does not establish that it does.

- **PPTX:** The backend reads slide text, tables, notes and editable charts. When LibreOffice/soffice is available and a model is selected, it renders full slides and sends one page image per slide to the selected multimodal LLM. It keeps both native and model-interpreted content, tagged separately.
- **PDF:** PyMuPDF reads page text; image-heavy, scanned or diagram-rich pages are rendered for selective vision analysis. Pages without text or successful vision are recorded as uncovered.
- **DOCX:** Reads paragraphs and tables in document order; vision describes supported embedded raster images.
- **Markdown/TXT:** Extracted directly; no calls to a vision model are needed. Markdown section headers and fenced code are retained.
- **Standalone images:** Require the selected vision-capable LLM to extract meaningful content.

Use **Library → Check vision support** to check that the backend can actually launch LibreOffice (not just find it on PATH) and that a chat model is selected. Actual image compatibility is checked when processing. Each file shows processed units, vision-analyzed units and extraction warnings; use **Reprocess** after changing the chat model or installing LibreOffice. You can inspect per-unit extraction coverage through `GET /api/courses/{course_id}/documents/{document_id}/coverage`.

**Windows LibreOffice troubleshooting:** QuestGen searches the backend's PATH, the usual `C:\Program Files\LibreOffice\program\soffice.exe` install location, and LibreOffice registry entries. If it cannot detect a portable/custom install, configure `QUESTGEN_LIBREOFFICE_CMD` in the **backend process** to the complete path to `soffice.exe`, restart FastAPI, and click **Library → Check vision support**. A Windows PowerShell session can verify the installation with `& "C:\Program Files\LibreOffice\program\soffice.exe" --version`. Installing LibreOffice in the browser/Next.js machine does not help if FastAPI runs in a container or on a different server; install it inside the backend runtime. Successful executable detection alone does not imply all slides were processed; **Reprocess** existing PowerPoints and verify the visual coverage count. The selected model must also accept image inputs. Errors from LibreOffice conversion are reported separately from missing installation.

## Storage and privacy

By default, local application state is under `backend/data/`:

- `questgen.sqlite3`: conversations, courses, indexed chunks, question drafts, versions and configuration.
- `uploads/`: course files stored on disk.
- `.questgen-provider.key`: encryption key for saved provider API credentials.
- `deepagents-checkpoints.sqlite3`: local Deep Agents checkpoint data.

The database location can be changed with `QUESTGEN_DB_PATH`, and uploads with `QUESTGEN_UPLOAD_DIR`. The Next.js API rewrite target can be changed with `QUESTGEN_API_ORIGIN` (default `http://127.0.0.1:8000`). API calls from the frontend are proxied through `/api/workspace/...`.

## License

See [`LICENSE`](LICENSE).
