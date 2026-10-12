"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ArrowRight, Check, ChevronDown, ClipboardList, Loader2, PencilLine, ShieldCheck } from "lucide-react";
import type { CourseMaterial } from "@/components/workspace/DocumentPanel";
import ExamChoiceSelect from "@/components/workspace/ExamChoiceSelect";

export type ExamFormat = { kind: string; count: number; marks_each: number };
export type ExamRequirements = {
  faculty_initials?: string;
  university_name?: string;
  exam_type?: string;
  total_marks?: number;
  difficulty?: string;
  question_formats?: ExamFormat[];
  material_scope?: string;
};
export type ExamSession = {
  thread_id: string;
  status: "not_started" | "collecting" | "awaiting_approval" | "approved";
  requirements: ExamRequirements;
  document_ids: string[];
  context_summary: string;
  missing: string[];
  ready_for_approval: boolean;
  updated_at?: number;
};

const FIELDS = ["faculty_initials", "university_name", "exam_type", "total_marks", "difficulty", "question_formats", "material_scope"] as const;
const LABELS: Record<string, string> = {
  faculty_initials: "Faculty initials", university_name: "University", exam_type: "Exam type",
  total_marks: "Total marks", difficulty: "Difficulty", question_formats: "Question sections", material_scope: "Materials",
};
const QUESTION_TEXT: Record<string, string> = {
  faculty_initials: "First, what initials should I put beside the faculty name on the exam paper?",
  university_name: "Which university should appear on the question paper?",
  exam_type: "What kind of exam are we preparing?",
  total_marks: "How many marks should the entire paper be worth?",
  difficulty: "How challenging would you like the questions to be?",
  question_formats: "How would you like to divide the marks between different question types?",
  material_scope: "Should I use your uploaded course materials or general knowledge?",
  document_ids: "Which of your uploaded documents should I use for this exam?",
  marks_distribution: "The section marks don't match the total. Would you like to adjust the question distribution?",
};
const KINDS = ["mcq", "short_answer", "long_answer", "problem_solving", "custom"];
const pretty = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
const formatSummary = (formats: ExamFormat[]) => formats.map(f => `${f.count} ${pretty(f.kind)} × ${f.marks_each}`).join(" · ");

/** The marks total alone is not sufficient: empty/invalid rows used to fail silently. */
export function sectionMixError(formats: ExamFormat[], totalMarks?: number): string | null {
  const cents = (marks: number) => Math.round(marks * 100);
  const validMarks = (marks: number, maximum: number) =>
    Number.isFinite(marks) && marks >= 0.01 && marks <= maximum &&
    Math.abs(marks * 100 - cents(marks)) < 1e-7;
  if (totalMarks === undefined || !validMarks(totalMarks, 1000)) return "Set the exam's total marks first.";
  if (!formats.length) return "Add at least one question type.";
  if (formats.length > 12) return "Use at most 12 question types.";
  for (const [index, section] of formats.entries()) {
    if (!KINDS.includes(section.kind)) return `Choose a valid type for section ${index + 1}.`;
    if (!Number.isInteger(section.count) || section.count < 1 || section.count > 100) {
      return `Section ${index + 1} needs 1–100 questions. Remove an unused type with ×.`;
    }
    if (!validMarks(section.marks_each, 100)) {
      return `Section ${index + 1} needs 0.01–100 marks per question (up to 2 decimal places).`;
    }
  }
  const allocatedCents = formats.reduce((sum, section) => sum + section.count * cents(section.marks_each), 0);
  if (allocatedCents !== cents(totalMarks)) return `The sections add up to ${allocatedCents / 100} marks; your exam needs ${totalMarks}.`;
  return null;
}

function suggestions(field: string, total: number | undefined, selected: number): {label: string; answer: string; updates: Partial<ExamRequirements>; documentIds?: string[]}[] {
  if (field === "exam_type") return ["Quiz", "Midterm", "Final", "Assignment", "Practice", "Custom"].map(label => ({label, answer: label, updates: {exam_type: label.toLowerCase()}}));
  if (field === "difficulty") return [
    ["Hard++", "Extreme challenge", "hard++"], ["Complex", "Advanced / analytical", "complex"],
    ["Moderate", "Balanced", "moderate"], ["Easy", "Foundational", "easy"],
  ].map(([name, description, value]) => ({label: `${name} · ${description}`, answer: name, updates: {difficulty: value}}));
  if (field === "material_scope") return [
    {label: "All uploaded materials", answer: "All course materials", updates: {material_scope: "all_course"}},
    {label: "Choose specific files", answer: "Selected files", updates: {material_scope: "selected"}},
    {label: "General knowledge", answer: "General knowledge", updates: {material_scope: "general"}},
  ];
  if (field === "total_marks") return [20, 40, 60, 100].map(number => ({label: `${number} marks`, answer: `${number} marks`, updates: {total_marks: number}}));
  if ((field === "question_formats" || field === "marks_distribution") && total && total > 0) {
    const options: {label: string; formats: ExamFormat[]}[] = [];
    if (Number.isInteger(total) && total <= 100) options.push({label: `MCQs · ${total} × 1`, formats: [{kind:"mcq", count:total, marks_each:1}]});
    if (total % 5 === 0 && total / 5 <= 100) options.push({label: `Short answers · ${total / 5} × 5`, formats: [{kind:"short_answer",count:total / 5,marks_each:5}]});
    if (total > 10 && (total - 10) % 5 === 0 && (total - 10) / 5 <= 100) options.push({label: "MCQ + short answer mix", formats: [{kind:"mcq",count:10,marks_each:1},{kind:"short_answer",count:(total-10)/5,marks_each:5}]});
    return options.filter(option => option.formats.every(f => f.count > 0)).map(option => ({label:option.label,answer:formatSummary(option.formats),updates:{question_formats:option.formats}}));
  }
  if (field === "document_ids" && selected) return [{label:`Use ${selected} selected ${selected === 1 ? "file" : "files"}`,answer:`Use ${selected} selected ${selected === 1 ? "file" : "files"}`,updates:{}}];
  return [];
}

export default function ExamConfiguration({ session, documents, selectedDocuments, busy, onChoose, onSave, onApprove, onSelectMaterials, controlsTarget, children }: {
  session: ExamSession | null;
  documents: CourseMaterial[];
  selectedDocuments: CourseMaterial[];
  busy: boolean;
  onChoose: (updates: Partial<ExamRequirements>, answer: string, documentIds?: string[]) => Promise<boolean>;
  onSave: (updates: Partial<ExamRequirements>, documentIds?: string[]) => Promise<void>;
  onApprove: () => Promise<void>;
  onSelectMaterials: () => void;
  controlsTarget?: HTMLElement | null;
  children?: ReactNode;
}) {
  const [custom, setCustom] = useState(false);
  const [savingMix, setSavingMix] = useState(false);
  const [mixError, setMixError] = useState("");
  const savingMixRef = useRef(false);
  const [formats, setFormats] = useState<ExamFormat[]>([{kind: "mcq",count:10,marks_each:1}]);
  const [showDetails, setShowDetails] = useState(false);
  const requirements = session?.requirements ?? {};
  const current = session?.missing[0] ?? null;
  const approved = session?.status === "approved";
  const ready = session?.ready_for_approval && !approved;
  const activeField = current;

  useEffect(() => { setCustom(false); setMixError(""); }, [current, session?.thread_id]);
  useEffect(() => {
    setFormats(requirements.question_formats?.length ? requirements.question_formats : [{kind:"mcq",count:10,marks_each:1}]);
  }, [session?.updated_at, requirements.question_formats]);

  const presets = suggestions(activeField || "", requirements.total_marks, selectedDocuments.length);
  const submitPreset = (item: (typeof presets)[number]) => {
    void onChoose(item.updates, item.answer, activeField === "document_ids" ? selectedDocuments.map(d => d.id) : undefined);
  };
  const validationError = sectionMixError(formats, requirements.total_marks);
  const editFormats = (update: (previous: ExamFormat[]) => ExamFormat[]) => {
    setFormats(update);
    setMixError("");
  };
  const saveCustom = async () => {
    // Also validate on click: never silently ignore an invalid distribution.
    const invalid = sectionMixError(formats, requirements.total_marks);
    if (invalid) { setMixError(invalid); return; }
    if (busy || savingMixRef.current) return;
    savingMixRef.current = true;
    setSavingMix(true);
    setMixError("");
    try {
      const saved = await onChoose({question_formats: formats.map(section => ({...section}))}, formatSummary(formats));
      if (saved) setCustom(false);
      else setMixError("The section mix was not saved. Please try again.");
    } catch (error) {
      setMixError(error instanceof Error ? error.message : "The section mix was not saved. Please try again.");
    } finally {
      savingMixRef.current = false;
      setSavingMix(false);
    }
  };

  // Keep the professor's question in the HITL bubble. Move interactive controls
  // into the composer surface without nesting forms or resetting editor state.
  const controls = (
    <div className="qg-hitl-controls-inner">
      {approved ? children : ready ? (
        <button className="qg-exam-confirm-details" type="button" disabled={busy} onClick={() => void onApprove()}><Check size={15}/> Confirm exam details <ArrowRight size={14}/></button>
      ) : (
        <div key={activeField || "intro"} className="qg-exam-control-step">
          {!!presets.length && <div className="qg-exam-quick-replies" role="group" aria-label="Suggested replies">
            {presets.map((item, index) => <button key={`${activeField}-${index}`} type="button" disabled={busy} onClick={() => submitPreset(item)}>{item.label} <ArrowRight size={13}/></button>)}
          </div>}
          {activeField === "document_ids" && <button type="button" className="qg-exam-quiet-action" onClick={onSelectMaterials} disabled={busy}><ClipboardList size={14}/> Browse uploaded materials ({documents.filter(d => d.status === "ready").length} ready)</button>}
          {(["question_formats", "marks_distribution"].includes(activeField || "")) && <>
            <button type="button" className="qg-exam-quiet-action" onClick={() => { setMixError(""); setCustom(v => !v); }} disabled={busy || savingMix}><PencilLine size={14}/> {custom ? "Hide section editor" : "Make a custom distribution"} <ChevronDown size={13}/></button>
            {custom && <div className="qg-exam-inline-sections" aria-label="Custom question distribution">
              {formats.map((format, index) => <div className="qg-exam-inline-row" key={index}>
                <ExamChoiceSelect label={`Question type ${index + 1}`} value={format.kind} options={KINDS.map(kind => ({value:kind,label:pretty(kind)}))} disabled={busy || savingMix} onChange={kind => editFormats(previous => previous.map((item,i) => i === index ? {...item,kind} : item))}/>
                <input aria-label={`Question count ${index + 1}`} type="number" min="1" max="100" value={format.count} disabled={busy || savingMix} onChange={event => editFormats(previous => previous.map((item,i) => i === index ? {...item,count:Number(event.target.value)} : item))}/>
                <span>×</span><input aria-label={`Marks per question ${index + 1}`} type="number" min="0.01" max="100" step="0.01" value={format.marks_each} disabled={busy || savingMix} onChange={event => editFormats(previous => previous.map((item,i) => i === index ? {...item,marks_each:Number(event.target.value)} : item))}/>
                <button aria-label={`Remove section ${index + 1}`} disabled={busy || savingMix || formats.length === 1} type="button" onClick={() => editFormats(previous => previous.filter((_,i) => i !== index))}>×</button>
              </div>)}
              <div className="qg-exam-inline-footer"><button type="button" disabled={busy || savingMix || formats.length >= 12} onClick={() => editFormats(previous => [...previous,{kind:"short_answer",count:1,marks_each:5}])}>+ Add type</button><span>{Math.round(formats.reduce((sum,f) => sum + f.count*f.marks_each, 0) * 100) / 100} / {requirements.total_marks} marks</span><button type="button" aria-label="Use this mix" disabled={busy || savingMix || !!validationError} onClick={() => void saveCustom()}>{savingMix ? <><Loader2 size={12} className="qg-spin" aria-hidden="true"/> Saving…</> : "Use this mix"}</button></div>
              {(mixError || validationError) && <p className="qg-exam-mix-feedback" role="status">{mixError || validationError}</p>}
            </div>}
          </>}
          {(["faculty_initials","university_name","total_marks"].includes(activeField || "")) && <small className="qg-exam-reply-tip">You can type your answer below.</small>}
        </div>
      )}
      {!!Object.keys(requirements).length && <details className="qg-exam-saved-details" open={showDetails} onToggle={e => setShowDetails(e.currentTarget.open)}>
        <summary>Exam details <ChevronDown size={13}/></summary>
        <div className="qg-exam-detail-list">{FIELDS.filter(field => requirements[field] !== undefined).map(field => <div key={field}><span>{LABELS[field]}</span><strong>{field === "question_formats" ? formatSummary(requirements.question_formats || []) : (["exam_type", "difficulty", "material_scope"].includes(field) ? pretty(String(requirements[field] ?? "")) : String(requirements[field] ?? ""))}</strong><button type="button" disabled={busy} onClick={() => { void onSave({[field]:null} as unknown as Partial<ExamRequirements>);}} aria-label={`Change ${LABELS[field]}`}>Change</button></div>)}</div>
      </details>}
      {!!session?.context_summary && <details className="qg-exam-context-brief"><summary>Using context from this conversation</summary><p>{session.context_summary}</p></details>}
    </div>
  );

  return <>
    <section className="qg-exam-assistant qg-exam-prompt-bubble" aria-label="QuestGen human-in-the-loop" aria-live={approved ? "off" : "polite"}>
      <div className="qg-exam-assistant-heading"><span className="qg-exam-assistant-avatar"><img src="/icon.svg" width={26} height={26} alt=""/></span><span>QuestGen</span>{busy && <Loader2 size={14} className="qg-spin" aria-label="Saving your answer"/>}</div>
      <div key={activeField || session?.status || "intro"} className="qg-exam-assistant-body qg-exam-prompt-reveal">
        <p>{approved ? "Your exam configuration is approved. Let's build your paper together." : ready ? "Everything I need is here. Shall I use these details to prepare your exam paper?" : QUESTION_TEXT[activeField || ""] || "Tell me about the exam you'd like to prepare."}</p>
      </div>
    </section>
    {controlsTarget ? createPortal(controls, controlsTarget) : null}
  </>;
}
