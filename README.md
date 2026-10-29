<p align="center">
  <img src="app/icon.svg" alt="QuestGen icon" width="88" height="88" />
</p>

<h1 align="center">QuestGen</h1>
<p align="center">An AI-powered, course-grounded assessment workspace for professors.</p>

QuestGen combines course-material chat, academic research, question drafting, human review, and printable exam creation in one conversation.

<p align="center">
 <img width="800" height="371" alt="Demo" src="https://github.com/user-attachments/assets/0ba192be-cf4b-488a-98a2-450a2da7a45c" />

</p>

## Workflows

- **Chat Mode** — Ask questions about course materials, receive cited, streamed answers, and revisit saved conversations.
- **Agent Mode** — Discuss assessment goals, research course or web sources when needed, approve a proposed plan, then generate and edit complete questions in chat.
- **Exam Mode** — Generate an exam directly or import selected Agent Mode drafts, organize the paper, approve it, and export student/instructor copies.

**Agent → Exam:** Discuss requirements → **Approve plan & draft questions** → Review/edit/select drafts → **Format selected in Exam Mode** → **Approve final paper** → Export PDF/DOCX. The handoff preserves selected questions without generating them again; replacing an existing paper requires confirmation.

**Direct Exam Mode:** Set requirements → Prepare and approve a blueprint → Generate questions in batches → Keep, edit, or selectively revise → Approve the paper → Export. Questions can include MCQs, short/long answers, and problem-solving formats. A model-assisted planner has a deterministic fallback.

**Human approval is required.** AI-generated questions remain drafts until reviewed. Changes to an approved plan or final paper require renewed approval.

https://github.com/user-attachments/assets/1268a2f4-5dc5-4abe-a3bd-0e4b947f038f

## Features

- Course-specific chats and material libraries; choose individual source files for retrieval.
- Upload **PDF, DOCX, PPTX, TXT, Markdown**, and common image formats.
- Native text extraction plus selective multimodal vision; PPTX slide rendering uses **LibreOffice** when available.
- Course-scoped retrieval through **SQLite FTS5**, optional **Ollama embeddings**, and page/slide citations where available.
- Streamed chat and agent activity; editable messages, targeted question revisions, version history, and restart recovery.
- **9Router, OpenAI, Anthropic, OpenRouter, and Ollama** model connections.
- Professor-approved **PDF/DOCX** exports: student copies omit solutions; instructor copies include answers and explanations.

## Architecture

<img width="3909" height="2622" alt="01_system_overview" src="https://github.com/user-attachments/assets/42378562-bbb2-4ad5-beba-88e5d1627091" />

## Quick start

**Requirements:** Node.js **20.9+**, npm, Python 3 (**3.11+ recommended**), and an accessible AI model/provider. Optional: [Ollama](https://ollama.com/) for local models and **LibreOffice** on the backend machine for rendered PowerPoint vision.

From the repository root, install Python dependencies:

**Windows (PowerShell)**
```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
```

Start the **backend**:
```bash
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

In a second terminal, start the **frontend**:
```bash
npm install
npm run dev
```

Open **http://localhost:3000/chat**. Backend health: **http://127.0.0.1:8000/api/health**.

## Models and document processing

Open **Model Settings**, configure a provider/API key or local endpoint, test the connection, and select a model. Environment variables are also supported: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `QUESTGEN_9ROUTER_URL`, `NINE_ROUTER_API_KEY`, and `QUESTGEN_OLLAMA_URL`.

**Model compatibility matters:** Agent Mode needs streamed tool calling; assessment generation needs appropriate structured responses; image analysis requires a vision-capable model. Being listed in the model picker does not guarantee these capabilities.

For slide-image analysis, install LibreOffice where **FastAPI** runs, then use **Library → Check vision support** and **Reprocess** existing uploads as needed. On custom Windows installations, set `QUESTGEN_LIBREOFFICE_CMD` to the full `soffice.exe` path.

## Local data and testing

Application data lives under `backend/data/` by default, including SQLite, uploads, and the local provider-credential encryption key. Protect **both** the database and the key file in backups. Override paths with `QUESTGEN_DB_PATH` and `QUESTGEN_UPLOAD_DIR`; set `QUESTGEN_API_ORIGIN` to change the frontend proxy target.

Run backend tests from the repository root:

```bash
PYTHONPATH=. pytest backend/tests -q
```

see [`LICENSE`](LICENSE).
