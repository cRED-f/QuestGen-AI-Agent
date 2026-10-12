"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown, Loader2, PenLine, RefreshCw, SkipForward } from "lucide-react";
import type { ExamRequirements } from "@/components/workspace/ExamConfiguration";
import ExamChoiceSelect from "@/components/workspace/ExamChoiceSelect";
import FormattedMessage from "@/components/workspace/FormattedMessage";

export type PaperSection = { id: string; kind: string; count: number; marks_each: number; topic: string; difficulty: string };
export type PaperBlueprint = { title: string; instructions: string; sections: PaperSection[] };
export type PaperQuestionContent = {
  stem: string; options: string[]; correct_answer: string; explanation: string;
  marks: number; kind: string; section_id: string; topic: string; difficulty: string;
  source_ids?: string[]; grounded?: boolean; evaluation?: { passed: boolean; issues: string[] };
};
export type ExamDraft = { id: string; slot: number; version: number; decision: "pending" | "keep" | "revise"; content: PaperQuestionContent };
export type PaperQuestion = { id: string; slot: number; position: number; version: number; content: PaperQuestionContent };
export type ExamPaper = {
  updated_at: number; thread_id: string; final_approved: boolean; final_approved_at: number | null; status: "awaiting_approval" | "ready" | "complete" | "generating";
  stale: boolean; blueprint: PaperBlueprint; candidate: PaperQuestionContent | null;
  candidate_id: string | null; candidate_slot: number | null;
  drafts: ExamDraft[]; questions: PaperQuestion[]; total_marks: number; accepted_marks: number;
  remaining_marks: number; total_questions: number;
  next_slot: { ordinal: number; kind: string; marks: number; topic: string; section_id: string } | null;
};
export type QuestionEdits = Pick<PaperQuestionContent, "stem" | "options" | "correct_answer" | "explanation">;

export function QuestionFields({ initial, onSave, onCancel, busy }: {
  initial: QuestionEdits; onSave: (change: QuestionEdits) => Promise<void>;
  onCancel: () => void; busy: boolean;
}) {
  const [value, setValue] = useState<QuestionEdits>(initial);
  useEffect(() => setValue(initial), [initial]);
  return <form className="qg-paper-editor" onSubmit={(event) => { event.preventDefault(); void onSave(value); }}>
    <label>Question<textarea required minLength={5} maxLength={2500} value={value.stem} onChange={event => setValue(old => ({...old, stem: event.target.value}))}/></label>
    {value.options.length > 0 && <div className="qg-paper-option-grid">{value.options.map((option, index) =>
      <label key={index}>Option {index+1}<input required value={option} onChange={event => setValue(old => ({...old, options: old.options.map((x,i) => i===index ? event.target.value : x), correct_answer: old.correct_answer === old.options[index] ? event.target.value : old.correct_answer}))}/></label>)}</div>}
    <label>Correct answer {value.options.length > 0 ?
      <ExamChoiceSelect label="Correct answer" value={value.correct_answer} disabled={busy} onChange={answer => setValue(old => ({...old,correct_answer:answer}))} options={value.options.map((option, i) => ({value:option,label:`${String.fromCharCode(65+i)}. ${option}`}))}/> : <textarea required value={value.correct_answer} onChange={event => setValue(old => ({...old, correct_answer: event.target.value}))}/>}</label>
    <label>Explanation<textarea required value={value.explanation} onChange={event => setValue(old => ({...old, explanation: event.target.value}))}/></label>
    <div className="qg-paper-actions"><button type="submit" disabled={busy}>Save edits</button><button type="button" onClick={onCancel}>Cancel</button></div>
  </form>;
}

export default function ExamPaperBuilder({ paper, requirements, busy, reasoning, onPrepare, onChangeBlueprint, onApproveBlueprint,
  onNext, onAccept, onRegenerate, onSkip, onEditCandidate, onCancelGeneration,
  onBatch, onReviseBatch, onChooseDraft, onEditDraft, onApproveDrafts }: {
  paper: ExamPaper | null; requirements: ExamRequirements; busy: boolean; reasoning?: string;
  onPrepare: () => Promise<void>; onChangeBlueprint: (blueprint: PaperBlueprint) => Promise<void>;
  onApproveBlueprint: () => Promise<void>; onNext: () => Promise<void>;
  onAccept: () => Promise<void>; onRegenerate: () => Promise<void>; onSkip: () => Promise<void>;
  onEditCandidate: (changes: QuestionEdits) => Promise<void>; onCancelGeneration: () => Promise<void>;
  onBatch: () => Promise<void>;
  onReviseBatch: (ids: string[], instructions: string) => Promise<void>;
  onChooseDraft: (id: string, decision: "keep" | "revise", version: number) => Promise<void>;
  onEditDraft: (id: string, version: number, changes: QuestionEdits) => Promise<void>;
  onApproveDrafts: (drafts: {id: string; version: number}[]) => Promise<void>;

}) {
  const [blueprint, setBlueprint] = useState<PaperBlueprint | null>(null);
  const [editingCandidate, setEditingCandidate] = useState(false);
  const [editingDraft, setEditingDraft] = useState<string | null>(null);
  const [revisionInstructions, setRevisionInstructions] = useState("");
  useEffect(() => { if (paper) setBlueprint(paper.blueprint); setEditingCandidate(false); }, [paper?.updated_at, paper?.thread_id]);
  if (!paper || paper.stale) return <section className="qg-builder" aria-label="Prepare exam paper">
    <div className="qg-builder-heading"><h2>Build your exam paper</h2><p>Your configuration is approved. Prepare a blueprint and review its topics before generating questions.</p></div>
    <button className="qg-paper-primary" type="button" disabled={busy} onClick={() => void onPrepare()}>{busy ? <Loader2 className="qg-spin" size={16}/> : <PenLine size={16}/>} Prepare paper blueprint</button>
  </section>;
  if (paper.status === "awaiting_approval") return <section className="qg-builder" aria-label="Review exam blueprint">
    <div className="qg-builder-heading"><span className="qg-overline">PROFESSOR REVIEW</span><h2>Approve the exam blueprint</h2>
      <p>Review exam coverage. Leave complexity on AI inferred unless you want a specific difficulty. Counts and marks stay linked to your approved configuration.</p></div>
    {blueprint && <form onSubmit={(event) => { event.preventDefault(); void onChangeBlueprint(blueprint); }} className="qg-blueprint-form">
      <label>Paper title<input required value={blueprint.title} onChange={e => setBlueprint(old => old && {...old, title: e.target.value})}/></label>
      <label>Instructions<textarea value={blueprint.instructions} onChange={e => setBlueprint(old => old && {...old, instructions: e.target.value})}/></label>
      {blueprint.sections.map((section, index) => <div className="qg-blueprint-section" key={section.id}>
        <strong>Section {index+1}: {section.kind.replaceAll("_", " ")}</strong>
        <small>{section.count} × {section.marks_each} marks</small>
        <label>Topic / scope<input required minLength={2} maxLength={200} value={section.topic} onChange={e => setBlueprint(old => old && {...old, sections: old.sections.map((x,i) => i===index ? {...x, topic: e.target.value} : x)})}/></label>
        <label>Difficulty<ExamChoiceSelect label={`Difficulty for section ${index + 1}`} value={section.difficulty} disabled={busy} options={["auto", "easy", "moderate", "complex", "hard++"].map(value => ({value,label:value === "auto" ? "AI inferred from course context" : value === "hard++" ? "Hard++" : value.charAt(0).toUpperCase() + value.slice(1)}))} onChange={difficulty => setBlueprint(old => old && {...old, sections: old.sections.map((x,i) => i===index ? {...x,difficulty} : x)})}/></label>
      </div>)}
      <div className="qg-paper-actions"><button type="submit" disabled={busy}>Save blueprint changes</button>
        <button type="button" className="qg-paper-primary" disabled={busy || JSON.stringify(blueprint) !== JSON.stringify(paper.blueprint)} onClick={() => void onApproveBlueprint()}><Check size={15}/> Approve & start building</button></div>
      {JSON.stringify(blueprint) !== JSON.stringify(paper.blueprint) && <small>Save your changes before approval.</small>}
    </form>}
  </section>;
  const candidate = paper.candidate;
  const drafts = paper.drafts || [];
  const kept = drafts.filter(draft => draft.decision === "keep");
  const flagged = drafts.filter(draft => draft.decision === "revise");
  const unfilled = paper.total_questions - paper.questions.length - drafts.length;
  if (!candidate && (drafts.length > 0 || paper.status === "generating" || paper.status === "ready") && paper.status !== "complete") return <section className="qg-builder" aria-label="Review generated exam">
    <div className="qg-builder-heading"><span className="qg-overline">CONTEXT-ENGINEERED EXAM STUDIO</span><h2>Review the complete exam draft</h2>
      <p>{paper.questions.length} approved · {drafts.length} drafts · {Math.max(0,unfilled)} to generate. Question complexity is inferred unless you specify it.</p></div>
    {paper.status === "generating" && <div className="qg-generation-status" role="status"><Loader2 className="qg-spin" size={16}/> Generating your question batch… Valid drafts are saved as they finish.
      <button type="button" onClick={() => void onCancelGeneration()}>Cancel generation</button>
      {reasoning && <details className="qg-reasoning" open><summary>Model thinking</summary><div className="qg-reasoning-body">{reasoning}</div></details>}</div>}
    {unfilled > 0 && paper.status !== "generating" && <button className="qg-paper-primary" type="button" disabled={busy} onClick={() => void onBatch()}>
      {busy ? <Loader2 className="qg-spin" size={16}/> : <PenLine size={16}/>} Generate {unfilled} questions in a batch</button>}
    {drafts.length > 0 && <>
      <div className="qg-paper-actions"><strong>{kept.length} keep · {flagged.length} revise · {drafts.length - kept.length - flagged.length} undecided</strong>
        <button type="button" disabled={busy || kept.length === 0 || paper.status === "generating"} onClick={() => void onApproveDrafts(kept.map(d => ({id:d.id, version:d.version})))}><Check size={15}/> Approve {kept.length} kept</button></div>
      <div className="qg-batch-drafts">{drafts.map(draft => <article key={draft.id} className="qg-batch-draft">
        <div className="qg-candidate-meta"><strong>Question {draft.slot} · {draft.content.kind.replaceAll("_", " ")}</strong><span>{draft.content.marks} marks · {draft.content.difficulty} · {draft.content.topic}</span></div>
        {editingDraft === draft.id ? <QuestionFields initial={draft.content} busy={busy}
          onSave={async changes => { await onEditDraft(draft.id, draft.version, changes); setEditingDraft(null); }} onCancel={() => setEditingDraft(null)}/> : <>
          <div className="qg-question-rich questgen-prose"><FormattedMessage content={draft.content.stem}/></div>
          {draft.content.options?.length > 0 && <ol className="qg-candidate-options" type="A">{draft.content.options.map((option,i) => <li key={i}>{option}</li>)}</ol>}
          <details className="qg-candidate-answer"><summary>Suggested answer, explanation and evidence <ChevronDown size={14}/></summary>
            <p><strong>Answer:</strong> {draft.content.correct_answer}</p><p>{draft.content.explanation}</p>
            <p>{draft.content.grounded ? `${draft.content.source_ids?.length || 0} cited source(s)` : "No citation; verify academic accuracy"}</p></details>
          <div className="qg-paper-actions"><button type="button" className={draft.decision === "keep" ? "qg-paper-primary" : ""} disabled={busy || paper.status === "generating"} onClick={() => void onChooseDraft(draft.id, "keep", draft.version)}><Check size={14}/> Keep</button>
            <button type="button" aria-pressed={draft.decision === "revise"} disabled={busy || paper.status === "generating"} onClick={() => void onChooseDraft(draft.id, "revise", draft.version)}><RefreshCw size={14}/> Revise</button>
            <button type="button" disabled={busy || paper.status === "generating"} onClick={() => setEditingDraft(draft.id)}><PenLine size={14}/> Edit</button></div>
        </>}
      </article>)}</div>
      {flagged.length > 0 && <div className="qg-batch-revision"><label>How should the selected questions change? (Optional)<textarea value={revisionInstructions} maxLength={1200} onChange={e => setRevisionInstructions(e.target.value)} placeholder="Example: Make these more analytical and change the numerical examples."/></label>
        <button className="qg-paper-primary" type="button" disabled={busy || paper.status === "generating"} onClick={() => void onReviseBatch(flagged.map(d => d.id), revisionInstructions)}><RefreshCw size={15}/> Regenerate only {flagged.length} flagged question(s)</button></div>}
    </>}
    {paper.questions.length > 0 && <p className="qg-paper-done">Approved questions are stored separately and will not be overwritten by batch revision. Review them in the paper sidebar.</p>}
    {requirements.material_scope === "general" && <p className="qg-paper-warning">General knowledge mode: answers need independent academic verification.</p>}
  </section>;
  return <section className="qg-builder" aria-label="Generate questions">
    <div className="qg-builder-heading"><span className="qg-overline">EXAM QUESTION REVIEW</span>
      <h2>{paper.status === "complete" ? "All slots filled" : candidate ? `Review question ${paper.candidate_slot}` : `Next question ${paper.next_slot?.ordinal ?? ""}`}</h2>
      <p>{paper.questions.length} of {paper.total_questions} accepted · {paper.accepted_marks} of {paper.total_marks} marks</p></div>
    {paper.status === "generating" && <div className="qg-generation-status" role="status"><Loader2 size={16} className="qg-spin"/> Generating a question… <button type="button" onClick={() => void onCancelGeneration()}>Cancel generation</button>
      {reasoning && <details className="qg-reasoning" open><summary>Model thinking</summary><div className="qg-reasoning-body">{reasoning}</div></details>}</div>}
    {candidate ? <div key={paper.candidate_id ?? paper.candidate_slot ?? "candidate"} className="qg-candidate">
      <div className="qg-candidate-meta"><span>{paper.next_slot?.kind.replaceAll("_"," ")} · {paper.next_slot?.marks} marks</span><span>{paper.next_slot?.topic}</span></div>
      {editingCandidate ? <QuestionFields initial={candidate} busy={busy} onSave={async edits => { await onEditCandidate(edits); setEditingCandidate(false); }} onCancel={() => setEditingCandidate(false)}/> : <>
        <div className="qg-question-rich questgen-prose"><FormattedMessage content={candidate.stem}/></div>
        {candidate.options.length > 0 && <ol className="qg-candidate-options" type="A">{candidate.options.map((option,i) => <li key={i}>{option}</li>)}</ol>}
        <details className="qg-candidate-answer"><summary>Answer and sources <ChevronDown size={14}/></summary><p><strong>Answer:</strong> {candidate.correct_answer}</p><p>{candidate.explanation}</p>
          <p>{candidate.grounded ? "Sources cited from course material" : "No document citations; review academic correctness"}</p></details>
        <div className="qg-paper-actions"><button className="qg-paper-primary" type="button" disabled={busy} onClick={() => void onAccept()}><Check size={16}/> Accept</button>
          <button type="button" disabled={busy} onClick={() => void onRegenerate()}><RefreshCw size={15}/> Regenerate</button>
          <button type="button" disabled={busy} onClick={() => setEditingCandidate(true)}><PenLine size={15}/> Edit</button>
          <button type="button" disabled={busy} onClick={() => void onSkip()}><SkipForward size={15}/> Skip</button></div></>}
    </div> : paper.status === "complete" ? <p className="qg-paper-done">The paper is assembled. Review accepted questions in the sidebar; removing a question will reopen its slot.</p> : paper.status !== "generating" && <button className="qg-paper-primary" type="button" disabled={busy} onClick={() => void onNext()}>{busy ? <Loader2 size={16} className="qg-spin"/> : <PenLine size={16}/>} Generate next question</button>}
    {requirements.material_scope === "general" && <p className="qg-paper-warning">General knowledge scope: questions may have no uploaded-material references.</p>}
  </section>;
}
