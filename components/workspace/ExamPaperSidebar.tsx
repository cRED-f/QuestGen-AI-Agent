"use client";

import { useState } from "react";
import PaperActionDialog from "./PaperActionDialog";
import FormattedMessage from "@/components/workspace/FormattedMessage";
import { ArrowDown, ArrowUp, CheckCircle2, Download, Pencil, Trash2, FileText, Layers3, Eye, EyeOff } from "lucide-react";
import type { ExamPaper, QuestionEdits } from "./ExamPaperBuilder";
import type { ExamRequirements } from "./ExamConfiguration";
import { QuestionFields } from "./ExamPaperBuilder";

export default function ExamPaperSidebar({ paper, requirements, busy, onEdit, onRemove, onReorder, onFinalApprove, onDownload, onLayout }: {
  paper: ExamPaper | null; requirements: ExamRequirements; busy: boolean;
  onEdit: (id: string, changes: QuestionEdits) => Promise<void>;
  onRemove: (id: string) => Promise<void>;
  onReorder: (ids: string[]) => Promise<void>;
  onFinalApprove: () => Promise<boolean>;
  onLayout?: (title: string, instructions: string) => Promise<void>;
  onDownload: (audience: "student" | "instructor", format: "pdf" | "docx") => Promise<void>;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingLayout, setEditingLayout] = useState(false);
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [showAnswers, setShowAnswers] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalError, setApprovalError] = useState("");
  const approveFinalPaper = async () => {
    if (approvalBusy || busy) return;
    setApprovalBusy(true);
    setApprovalError("");
    try {
      if (await onFinalApprove()) setApprovalOpen(false);
      else setApprovalError("Approval could not be completed. Check the workspace message and try again.");
    } catch (cause) {
      setApprovalError(cause instanceof Error ? cause.message : "Could not approve paper.");
    } finally { setApprovalBusy(false); }
  };
  const download = async (audience: "student" | "instructor", format: "pdf" | "docx") => {
    setDownloadBusy(true);
    try { await onDownload(audience, format); } finally { setDownloadBusy(false); }
  };
  if (!paper || paper.stale) return <aside className="qg-paper-sidebar" aria-label="Live exam paper preview">
    <div className="qg-paper-studio-header"><span className="qg-paper-studio-icon"><FileText size={18}/></span><div><span className="qg-paper-studio-eyebrow">LIVE DOCUMENT</span><h2>Your question paper</h2></div></div>
    <div className="qg-paper-studio-empty"><Layers3 size={26}/><strong>Ready to take shape</strong><p>Accept a question to see it appear here. Your changes are saved as you build.</p></div>
  </aside>;
  const move = (id: string, direction: -1 | 1) => {
    const ids = paper.questions.map(q => q.id);
    const index = ids.indexOf(id), target = index + direction;
    if (target < 0 || target >= ids.length) return;
    if (paper.questions[index].content.section_id !== paper.questions[target].content.section_id) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    void onReorder(ids);
  };
  const completion = paper.total_marks ? Math.min(100, Math.max(0, 100*paper.accepted_marks/paper.total_marks)) : 0;
  return <aside className="qg-paper-sidebar" aria-label="Live exam paper preview">
    <div className="qg-paper-studio-header">
      <span className="qg-paper-studio-icon"><FileText size={18}/></span>
      <div><span className="qg-paper-studio-eyebrow">LIVE DOCUMENT · AUTOSAVED</span><h2>Your question paper</h2></div>
      <span className="qg-paper-studio-status">{paper.final_approved ? "Approved" : "Draft"}</span>
    </div>
    <div className="qg-paper-studio-stats" aria-label="Exam paper progress">
      <div><strong>{paper.questions.length}<span>/{paper.total_questions}</span></strong><small>Questions</small></div>
      <div><strong>{paper.accepted_marks}<span>/{paper.total_marks}</span></strong><small>Marks</small></div>
      <div><strong>{Math.round(completion)}<span>%</span></strong><small>Complete</small></div>
    </div>
    <div className="qg-paper-meter" role="progressbar" aria-valuenow={Math.round(completion)} aria-valuemin={0} aria-valuemax={100} aria-label="Paper completion"><span style={{width: `${completion}%`}}/></div>
    <div className="qg-paper-preview-label"><span><Eye size={14}/> LIVE PREVIEW</span><small>Updated on acceptance</small></div>
    {onLayout && <div className="qg-paper-layout-tools">
      <button type="button" disabled={busy} onClick={() => {setEditingLayout(value => !value);setTitle(paper.blueprint.title);setInstructions(paper.blueprint.instructions);}}>
        <Pencil size={13}/> Edit paper title & instructions
      </button>
      {editingLayout && <form className="qg-paper-editor" onSubmit={event => {
        event.preventDefault(); void onLayout(title, instructions).then(() => setEditingLayout(false));
      }}>
        <label>Paper title<input required minLength={3} maxLength={160} value={title} onChange={event => setTitle(event.target.value)}/></label>
        <label>Instructions<textarea maxLength={800} value={instructions} onChange={event => setInstructions(event.target.value)}/></label>
        <div className="qg-paper-actions"><button disabled={busy} type="submit">Save layout</button><button type="button" onClick={() => setEditingLayout(false)}>Cancel</button></div>
      </form>}
    </div>}
    <div className="qg-paper-sheet">
      <header>{requirements.university_name && <span className="qg-sheet-university">{requirements.university_name}</span>}
        <h3>{paper.blueprint.title}</h3><small>{requirements.exam_type?.toUpperCase()}{requirements.faculty_initials ? ` · Faculty: ${requirements.faculty_initials}` : ""}</small>
        <small>Full marks: {paper.total_marks}</small></header>
      <p className="qg-sheet-instructions">{paper.blueprint.instructions}</p>
      {paper.blueprint.sections.map((section,i) => {
        const questions = paper.questions.filter(q => q.content.section_id === section.id);
        return <section key={section.id} className="qg-sheet-section">
          <h4>Section {i+1}: {section.kind.replaceAll("_"," ")} <span>{questions.length}/{section.count}</span></h4>
          {questions.length ? questions.map(question => <article key={question.id} className="qg-sheet-question">
            {editingId === question.id ? <QuestionFields initial={question.content} busy={busy} onSave={async change => {await onEdit(question.id,change); setEditingId(null);}} onCancel={() => setEditingId(null)}/> : <>
              <div className="qg-sheet-question-stem"><strong>Q{question.position}.</strong><div className="qg-question-rich questgen-prose"><FormattedMessage content={question.content.stem}/></div><b>({question.content.marks})</b></div>
              {question.content.options.length > 0 && <ol type="A">{question.content.options.map((option,j) => <li key={j}>{option}</li>)}</ol>}
              {showAnswers && <p className="qg-sheet-answer"><strong>Answer:</strong> {question.content.correct_answer}</p>}
              <div className="qg-sheet-controls">
                <button type="button" title="Move question up" aria-label={`Move question ${question.position} up`} disabled={busy || paper.questions[paper.questions.findIndex(q => q.id === question.id)-1]?.content.section_id !== question.content.section_id} onClick={() => move(question.id,-1)}><ArrowUp size={13}/></button>
                <button type="button" title="Move question down" aria-label={`Move question ${question.position} down`} disabled={busy || paper.questions[paper.questions.findIndex(q => q.id === question.id)+1]?.content.section_id !== question.content.section_id} onClick={() => move(question.id,1)}><ArrowDown size={13}/></button>
                <button type="button" title="Edit accepted question" disabled={busy} onClick={() => setEditingId(question.id)}><Pencil size={13}/></button>
                <button type="button" title="Remove question" disabled={busy} onClick={() => {if (window.confirm("Remove this question from the paper?")) void onRemove(question.id);}}><Trash2 size={13}/></button>
                <small>v{question.version}</small>
              </div></>}
          </article>) : <p className="qg-sheet-empty">No accepted questions yet</p>}
        </section>;
      })}
    </div>
    <button type="button" className="qg-paper-answer-toggle" aria-pressed={showAnswers} onClick={() => setShowAnswers(old => !old)}>{showAnswers ? <EyeOff size={14}/> : <Eye size={14}/>} {showAnswers ? "Hide" : "Show"} answer key</button>
    <p className="qg-paper-sidebar-foot">{paper.remaining_marks} marks remaining · {paper.questions.length}/{paper.total_questions} accepted questions</p>
    {paper.status === "complete" && <section className="qg-final-review" aria-label="Final paper review and exports">
      <h3><CheckCircle2 size={16}/> Final professor review</h3>
      <p>Verify every question, mark, and option above. Only accepted questions are included. Academic correctness is your responsibility.</p>
      {paper.final_approved ? <div className="qg-approved-message" role="status">Approved for download. Editing the paper requires approval again.</div>
        : <button type="button" className="qg-paper-primary" disabled={busy} onClick={() => { setApprovalError(""); setApprovalOpen(true); }}>Approve final paper</button>}
      <div className="qg-export-grid" aria-label="Download exam copies">
        {(["student", "instructor"] as const).map(audience => (["pdf", "docx"] as const).map(format =>
          <button type="button" key={`${audience}-${format}`} disabled={!paper.final_approved || busy || downloadBusy}
            onClick={() => void download(audience, format)}><Download size={14}/> {audience === "student" ? "Student" : "Instructor"} {format.toUpperCase()}</button>
        ))}
      </div>
      {!paper.final_approved && <small>Approve the completed paper to enable PDF and DOCX downloads.</small>}
    </section>}
    {approvalOpen && !paper.final_approved && <PaperActionDialog
      eyebrow="EXAM MODE · FINAL REVIEW"
      title="Approve this question paper?"
      description="Confirm that you've reviewed the content, marks and answer options in the live preview."
      icon={<CheckCircle2 size={19}/>}
      details={[{label: "Questions", value: String(paper.questions.length)},
        {label: "Total marks", value: String(paper.total_marks)},
        {label: "Paper status", value: "Ready for export"}]}
      note="Any changes after approval will require another review before PDF or DOCX export."
      confirmLabel="Approve paper"
      error={approvalError}
      busy={busy || approvalBusy}
      onCancel={() => setApprovalOpen(false)}
      onConfirm={() => void approveFinalPaper()}
    />}
  </aside>;
}
