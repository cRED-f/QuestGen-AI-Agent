"use client";

import { useState } from "react";
import FormattedMessage from "@/components/workspace/FormattedMessage";
import { Check, ClipboardCheck, History, LockKeyhole, LockKeyholeOpen, PencilLine, RefreshCw, X } from "lucide-react";
import { type Source, sourceLink, sourceLocation } from "@/components/workspace/DocumentPanel";

export type AssessmentQuestion = {
  id: string;
  ordinal: number;
  version: number;
  locked: boolean;
  review_status: "pending" | "approved" | "rejected";
  review_note: string;
  versions: { version: number; change_type: string; instruction: string; created_at: number }[];
  content: {
    topic: string;
    kind: string;
    bloom_level: string;
    difficulty: string;
    marks: number;
    stem: string;
    options: string[];
    correct_answer: string;
    explanation: string;
    grounded: boolean;
    sources: Source[];
  };
};

type Props = {
  question: AssessmentQuestion;
  showAnswers: boolean;
  disabled: boolean;
  onChange: (id: string, path: string, method: "PUT" | "POST", body: object) => Promise<void>;
  fetchHistory: (id: string) => Promise<{ versions: { version: number; content: AssessmentQuestion["content"]; change_type: string; instruction: string; created_at: number }[] }>;
};

export default function QuestionEditor({ question: q, showAnswers, disabled, onChange, fetchHistory }: Props) {
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState<"revise" | "regenerate" | null>(null);
  const [prompt, setPrompt] = useState("");
  const [note, setNote] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<Awaited<ReturnType<Props["fetchHistory"]>> | null>(null);
  const [draft, setDraft] = useState({ stem: q.content.stem, options: [...q.content.options],
    correct_answer: q.content.correct_answer, explanation: q.content.explanation });
  const busy = disabled;
  const update = async (path: string, method: "PUT" | "POST", body: object) => {
    try {
      await onChange(q.id, path, method, { expected_version: q.version, ...body });
      setEditing(false); setMode(null); setPrompt("");
    } catch { /* Parent displays the API error; keep this form open for corrections. */ }
  };
  const openEditor = () => {
    setDraft({ stem: q.content.stem, options: [...q.content.options],
      correct_answer: q.content.correct_answer, explanation: q.content.explanation });
    setEditing(true); setMode(null);
  };
  const toggleHistory = async () => {
    if (historyOpen) { setHistoryOpen(false); return; }
    const value = await fetchHistory(q.id);
    setHistory(value); setHistoryOpen(true);
  };

  return <article className="qg-assessment-question" aria-label={`Question ${q.ordinal}`}>
    <div className="qg-assessment-question-meta">
      <span>Q{q.ordinal} · {q.content.kind.replaceAll("_", " ")} · {q.content.marks} marks</span>
      <span>v{q.version} · {q.content.bloom_level} · {q.review_status}{q.locked ? " · locked" : ""}</span>
    </div>
    <div className="qg-question-rich questgen-prose"><FormattedMessage content={q.content.stem}/></div>
    {!!q.content.options.length && <ol type="A">{q.content.options.map((option, i) => <li key={i}>{option}</li>)}</ol>}
    {showAnswers && <div className="qg-assessment-answer"><strong>Answer:</strong> {q.content.correct_answer}<p>{q.content.explanation}</p></div>}
    <div className="qg-assessment-citations">
      {q.content.sources?.length ? q.content.sources.map((source) => <a key={source.chunk_id} href={sourceLink(source)} target="_blank" rel="noopener noreferrer">{source.filename}{source.page ? ` · ${sourceLocation(source)}` : ""} ↗</a>) : <span>Not grounded in uploaded material</span>}
    </div>
    <div className="qg-question-actions">
      <button type="button" disabled={busy || q.locked} onClick={openEditor}><PencilLine size={14}/> Edit</button>
      <button type="button" disabled={busy || q.locked} onClick={() => { setMode("revise"); setEditing(false); }}>AI revise</button>
      <button type="button" disabled={busy || q.locked} onClick={() => { setMode("regenerate"); setEditing(false); }}><RefreshCw size={14}/> Regenerate</button>
      <button type="button" disabled={busy} onClick={() => void update("lock?locked=" + (!q.locked), "PUT", {})}>
        {q.locked ? <LockKeyholeOpen size={14}/> : <LockKeyhole size={14}/>} {q.locked ? "Unlock" : "Lock"}
      </button>
      <button type="button" disabled={busy} onClick={() => void toggleHistory()}><History size={14}/> Versions</button>
    </div>
    {editing && <form className="qg-question-form" onSubmit={(event) => {
      event.preventDefault(); void update("", "PUT", draft);
    }}>
      <label>Question stem<textarea required minLength={15} maxLength={2500} value={draft.stem} onChange={e => setDraft({ ...draft, stem: e.target.value })}/></label>
      {draft.options.map((option, i) => <label key={i}>Option {String.fromCharCode(65+i)}<input required value={option} onChange={e => setDraft({ ...draft, options: draft.options.map((v,j) => i === j ? e.target.value : v) })}/></label>)}
      <label>Correct answer <textarea required value={draft.correct_answer} onChange={e => setDraft({ ...draft, correct_answer: e.target.value })}/></label>
      <label>Explanation <textarea required minLength={10} value={draft.explanation} onChange={e => setDraft({ ...draft, explanation: e.target.value })}/></label>
      <div className="qg-question-actions"><button type="submit" disabled={busy}><Check size={14}/> Save version</button><button type="button" onClick={() => setEditing(false)}><X size={14}/> Cancel</button></div>
    </form>}
    {mode && <form className="qg-question-form" onSubmit={(event) => {
      event.preventDefault(); void update(mode, "POST", { instruction: prompt });
    }}>
      <label>{mode === "revise" ? "Tell QuestGen exactly what to change" : "Describe the replacement question"}
        <textarea required minLength={3} maxLength={2000} value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="e.g. Make the distractors more challenging without changing the topic"/>
      </label>
      <div className="qg-question-actions"><button type="submit" disabled={busy || prompt.trim().length < 3}>Apply AI change</button><button type="button" onClick={() => setMode(null)}>Cancel</button></div>
    </form>}
    <div className="qg-question-review">
      <span>Professor review: <strong>{q.review_status}</strong></span>
      <button type="button" disabled={busy || q.review_status === "approved"} onClick={() => void update("review", "PUT", { decision: "approved" })}><ClipboardCheck size={14}/> Approve</button>
      <button type="button" disabled={busy || q.review_status === "rejected"} onClick={() => void update("review", "PUT", { decision: "rejected", note })}>Reject</button>
      <input aria-label={`Review note for question ${q.ordinal}`} maxLength={500} value={note} onChange={e => setNote(e.target.value)} placeholder="Optional review note"/>
    </div>
    {historyOpen && <div className="qg-question-history"><strong>Revision history (newest first)</strong>
      {(history?.versions || []).map(v => <details key={v.version}><summary>Version {v.version} · {v.change_type} · {new Date(v.created_at * 1000).toLocaleString()}</summary>
        <div className="qg-question-rich questgen-prose"><FormattedMessage content={v.content.stem}/></div><small>{v.instruction}</small>
        {v.version !== q.version && !q.locked && <div className="qg-question-actions"><button type="button" disabled={busy} onClick={() => void update("restore", "POST", {target_version: v.version})}>Restore as new version</button></div>}
      </details>)}
      {!history?.versions.length && <p>No version snapshots available yet.</p>}
    </div>}
  </article>;
}
