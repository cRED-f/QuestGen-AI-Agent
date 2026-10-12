# QuestGen

**A local-first, chat-based assessment workspace for professors.**

QuestGen lets you organize course materials, discuss them with an AI assistant, draft an assessment, revise individual questions, review and approve the results, and export student or instructor copies. Its interface uses a paper-and-pencil-inspired design rather than a landing-page-first workflow.

> **Status:** Active development. QuestGen is designed for **one trusted local user**. It intentionally has **no authentication**, and course separation is a data filter, **not a multi-user security boundary**. Do not expose the backend, model credentials, or uploaded documents on a public network.

## Current features

- **Chat workspace:** streamed model replies, persisted conversations, message editing in the main composer, response regeneration, and direct conversation deletion.
- **Courses:** create and switch courses; each has its own chat history and material Library.
- **Material Library:** upload, browse, attach, search, open, delete, diagnose OCR and reprocess files. Chat and assessment generation can retrieve from a selected subset of materials, or from the active course if none are selected.
- **Document extraction:** PDF, DOCX, PPTX, TXT, Markdown, PNG, JPG/JPEG, WEBP, BMP and TIFF. PowerPoint processing includes slide text, tables, speaker notes and embedded images; images use optional OCR and/or an Ollama vision model.
- **Retrieval and citations:** SQLite full-text search with optional Ollama embeddings, course filters, and page/slide references when available.
- **Model providers:** 9Router, OpenAI, Anthropic, OpenRouter and Ollama. Configure credentials/connection in Model Settings, then select an available model in chat.
- **Assessment drafts:** Deep Agents-based blueprint planning followed by bounded question generation, validation and review. SQLite stores the blueprint, completed questions, progress events and restart recovery state.
- **Question review:** edit or selectively regenerate a question, use conversational customization, lock/unlock, inspect versions, restore an older revision, and approve or reject individual questions.
- **Exports:** separate PDF/DOCX student and instructor copies. Student export requires approval of every question and excludes answer keys; instructor export includes answers and explanations.

Generated questions are **drafts**. AI academic reviews do not replace a professor's judgment or establish factual correctness.

## Implementation overview

| Layer | Current implementation |
| --- | --- |
| Interface | Next.js 16.4.0, React 19.3.0, TypeScript, Tailwind CSS |
| Backend | FastAPI, Python, Pydantic |
| AI | Provider HTTP streaming; Deep Agents supervisor for assessment blueprints; separate structured generation/review calls |
| Persistence | Local SQLite (courses, messages, documents, assessments, question versions, provider settings) |
| Retrieval | SQLite FTS5; optional Ollama embeddings; citations to stored passages |
| Extraction | pypdf, python-docx, python-pptx, Pillow, optional Tesseract and Ollama vision |
| Output | ReportLab PDF and python-docx DOCX |

Normal chat uses direct model streaming; **not every chat turn runs Deep Agents**. Deep Agents is used for planning a structured assessment. Generation and review then use bounded model calls. Work is processed by local in-process workers, not a distributed task queue.

## Prerequisites

- Node.js **20.9 or newer**, plus npm.
- A supported Python 3 installation and pip (Python **3.11+ recommended**).
- An AI provider endpoint and an available model. For local models, install and start [Ollama](https://ollama.com/).
- **Optional:** Tesseract OCR on the backend computer for images and embedded slide images; a compatible Ollama vision model for diagram descriptions.

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

**Dependency lockfile:** The previous `package-lock.json` was generated for Next.js 15 / React 19.0 and does not match the pinned Next.js 16.4.0 / React 19.3.0 manifest. It was removed rather than supplying an invalid or fabricated replacement. On a machine with npm registry access, `npm install` resolves packages and generates a new `package-lock.json`. **Commit that newly generated lockfile** for reproducible installs. Afterward, CI/other machines should use `npm ci`. The updated lockfile and a complete Next.js production build could not be generated or verified in the offline review environment.

After installing dependencies, verify them:

```bash
npm ls next react react-dom eslint-config-next --depth=0
npm run typecheck
npm run lint
npm run build
```

## Configure an AI provider

In QuestGen, open **Model Settings**, select a provider, configure the key and/or server address, and use the **Test connection** action. Then select an available model from the **chat model picker**. The backend stores configured credentials encrypted locally and does not send saved secret values back to the browser. Protect the database **and** its local encryption-key file when backing up or moving the application.

Environment variables are also supported (set them **before** starting FastAPI):

| Provider | Variables |
| --- | --- |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| 9Router | `QUESTGEN_9ROUTER_URL` (default `http://127.0.0.1:20128/v1`); optional `NINE_ROUTER_API_KEY` |
| Ollama | `QUESTGEN_OLLAMA_URL` (default `http://127.0.0.1:11434`); optional `QUESTGEN_OLLAMA_API_KEY` for protected proxies |

Anthropic generation currently uses an OpenAI-compatible API path, while model discovery uses its model-list endpoint. Availability in the picker **does not guarantee** that a model supports tool calling or structured assessment generation. Test the actual generation workflow with your chosen model.

## OCR, vision, and embeddings

For image text extraction, install the Tesseract executable, then check availability using **Course Materials → Check OCR**. On Windows, restart the backend after installing it; if automatic detection fails, set its path in the same PowerShell session where FastAPI starts:

```powershell
$env:QUESTGEN_TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

To describe diagrams with a local vision model, configure an **installed** Ollama-compatible model:

```powershell
$env:QUESTGEN_VISION_MODEL = "your-installed-vision-model"
```

For optional semantic retrieval, set:

```powershell
$env:QUESTGEN_EMBED_MODEL = "your-installed-embedding-model"
```

The embedding and vision pipelines use the configured Ollama connection. Without an embedding model, keyword search still works. Some images may not yield readable text even when Tesseract is correctly installed. PDF text extraction is supported, but **scanned PDFs with no embedded text are not yet OCR-processed**.

Supported uploads are limited to **15 MiB per file**. Other extraction limits apply to total text, images, and slide/page counts.

## Working with courses and assessments

1. Select or create a course, then upload its lectures in **Library**.
2. In chat, use **Materials** or **Attach course material** to choose previously uploaded files from the active course.
3. Chat about the content or generate an exam. Selected files limit retrieval for that chat request or assessment; with none selected, retrieval can search all indexed files in the course.
4. Inspect assessment generation progress and review the resulting draft.
5. Edit or regenerate individual questions, apply a targeted conversational instruction, lock questions that should stay unchanged, and approve the final set.
6. Export an approved **student** paper or an **instructor** copy with the answer key.

AI revisions and selective regeneration respect the assessment's saved document selection. If a selected document has been deleted or is no longer ready, further AI revision fails clearly instead of retrieving evidence from another file. A previously saved question version is not itself an assertion that its citations are still available.

## Storage and privacy

By default, local application state is under `backend/data/`:

- `questgen.sqlite3`: conversations, courses, indexed chunks, question drafts, versions and configuration.
- `uploads/`: course files stored on disk.
- `.questgen-provider.key`: encryption key for saved provider API credentials.
- `deepagents-checkpoints.sqlite3`: local Deep Agents checkpoint data.

The database location can be changed with `QUESTGEN_DB_PATH`, and uploads with `QUESTGEN_UPLOAD_DIR`. The Next.js API rewrite target can be changed with `QUESTGEN_API_ORIGIN` (default `http://127.0.0.1:8000`). API calls from the frontend are proxied through `/api/workspace/...`.

**Security limitation:** There is intentionally no authentication. Course separation does not protect different people from accessing one another's data. Do not expose this development backend publicly or use it as an unrestricted shared multi-professor service.

## Tests

```bash
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend/tests -q
```

The tests exercise persistence, provider configuration, course-scoped attachments, document ingestion and OCR diagnostics, generation persistence, customization, versioning, approvals and exports. They predominantly use mocked AI calls: passing tests **does not verify** live model quality or all browser interactions.

After regenerating the npm lockfile with registry access, run `npm run typecheck`, `npm run lint` and `npm run build` before committing a deployable change.

## Known limitations

- No user authentication, multi-user authorization or publicly safe credential-management API.
- No offline, professor-labeled evaluation benchmark or end-to-end browser test suite yet.
- No reliable promise that every selectable provider/model supports structured output and Deep Agents tool calling.
- No reusable cross-assessment question-bank editor or comprehensive assessment blueprint editor yet.
- No scanned-PDF OCR, managed job worker, Kubernetes deployment, or production monitoring stack.
- Content edits preserve existing question blueprint allocations such as marks, topic, type and Bloom's level.
- The current local worker model assumes a single backend process; do not run multiple backend workers against the same SQLite job queue.

## License

See [`LICENSE`](LICENSE).
