# QuestGen

**A chat-based assessment workspace for professors.**

QuestGen lets you organize course materials, discuss them with an AI assistant, draft an assessment, revise individual questions, review and approve the results, and export student or instructor copies.

> **Status:** Active development.

## Current features

- **Chat workspace:** streamed model replies, persisted conversations, message editing in the main composer, response regeneration, and direct conversation deletion.
- **Courses:** create and switch courses; each has its own chat history and material Library.
- **Material Library:** upload, browse, attach, search, open, delete, inspect extraction/vision coverage and reprocess files. Chat and assessment generation can retrieve from a selected subset of materials, or from the active course if none are selected.
- **Document extraction:** PDF, DOCX, PPTX, TXT, Markdown, PNG, JPG/JPEG, WEBP, BMP and TIFF. PowerPoint processing combines native slide text, tables, editable chart data and speaker notes with complete slide-image analysis using the selected multimodal model when slide rendering is available. PDF and DOCX use native-first, selective vision processing.
- **Retrieval and citations:** SQLite full-text search with optional Ollama embeddings, course filters, and page/slide references when available.
- **Model providers:** 9Router, OpenAI, Anthropic, OpenRouter and Ollama. Configure credentials/connection in Model Settings, then select an available model in chat.
- **Assessment drafts:** Deep Agents-based blueprint planning followed by bounded question generation, validation and review. SQLite stores the blueprint, completed questions, progress events and restart recovery state.
- **Question review:** edit or selectively regenerate a question, use conversational customization, lock/unlock, inspect versions, restore an older revision, and approve or reject individual questions.
- **Exports:** separate PDF/DOCX student and instructor copies. Student export requires approval of every question and excludes answer keys; instructor export includes answers and explanations.

Generated questions are **drafts**. AI academic reviews do not replace a professor's judgment or establish factual correctness.

## Implementation overview

| Layer       | Current implementation                                                                                                                    |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Interface   | Next.js 16.4.0, React 19.3.0, TypeScript, Tailwind CSS                                                                                    |
| Backend     | FastAPI, Python, Pydantic                                                                                                                 |
| AI          | Provider HTTP streaming; Deep Agents supervisor for assessment blueprints; separate structured generation/review calls                    |
| Persistence | Local SQLite (courses, messages, documents, assessments, question versions, provider settings)                                            |
| Retrieval   | SQLite FTS5; optional Ollama embeddings; citations to stored passages                                                                     |
| Extraction  | Unified `backend/ingestion.py` dispatcher; PyMuPDF, python-docx, python-pptx, Pillow, selected-model image analysis (`backend/vision.py`) |
| Output      | ReportLab PDF and python-docx DOCX                                                                                                        |

Normal chat uses direct model streaming; **not every chat turn runs Deep Agents**. Deep Agents is used for planning a structured assessment. Generation and review then use bounded model calls. Work is processed by local in-process workers, not a distributed task queue.

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
