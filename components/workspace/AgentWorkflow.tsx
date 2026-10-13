"use client";

import { ArrowRight, BookOpenText, Check, ClipboardList, FileCheck2, Loader2, X } from "lucide-react";
import FormattedMessage from "@/components/workspace/FormattedMessage";

export type AgentWork = {
  id: string;
  kind: "exam" | "question_bank" | "topic_coverage" | "research_summary" | "assessment_review";
  status: "proposed" | "approved" | "rejected";
  document_ids: string[];
  payload: {
    title: string; rationale: string; content: string; exam_type: string;
    sections: { topic_id: string; kind: string; count: number; marks_each: number }[];
    course_chunk_ids: string[]; web_source_ids: number[];
  };
  result: null | { type: string; content?: string; status?: string };
};

type Topic = { id: string; title: string };
type WebSource = { id: number; title: string; url: string };

export default function AgentWorkflow({ workflow, topics, sources, busy, onDecision, onRevise }: {
  workflow: AgentWork;
  topics: Topic[];
  sources: WebSource[];
  busy: boolean;
  onDecision: (approve: boolean) => Promise<void>;
  onRevise: () => void;
}) {
  const assessment = workflow.kind === "exam" || workflow.kind === "question_bank";
  const topicNames = new Map(topics.map(topic => [topic.id, topic.title]));
  const citedSources = workflow.payload.web_source_ids.map(id => sources.find(source => source.id === id)).filter((item): item is WebSource => Boolean(item));
  return <section className="qg-agent-workflow" aria-label="Academic work plan">
    <div className="qg-agent-workflow-top">
      <div className="qg-agent-workflow-icon">{assessment ? <ClipboardList size={19}/> : <BookOpenText size={19}/>}</div>
      <div className="qg-agent-workflow-title"><span className="qg-overline">{assessment ? "ASSESSMENT DESIGN" : "ACADEMIC RESEARCH"} · {workflow.status.replaceAll("_", " ")}</span><h3>{workflow.payload.title}</h3></div>
    </div>
    <p>{workflow.payload.rationale}</p>
    {assessment ? <>
      <div className="qg-agent-workflow-sections">
        {workflow.payload.sections.map((section, i) => <div key={`${section.topic_id}-${i}`}>
          <div><strong>{topicNames.get(section.topic_id) || "Selected topic"}</strong><small>{section.kind.replaceAll("_", " ")}</small></div>
          <span>{section.count} × {section.marks_each} marks</span>
        </div>)}
      </div>
      <small>Questions: {workflow.payload.sections.reduce((n,s) => n + s.count, 0)} · Total marks: {workflow.payload.sections.reduce((n,s) => n + s.count * s.marks_each, 0)} · {workflow.document_ids.length} selected files</small>
      {workflow.status === "approved" && <div className="qg-agent-workflow-note"><FileCheck2 size={16}/> Configuration accepted. The editable paper blueprint and generated questions still require separate professor approval.</div>}
    </> : <>
      <div className="qg-agent-workflow-report questgen-prose"><FormattedMessage content={workflow.payload.content}/></div>
      {(workflow.payload.course_chunk_ids.length > 0 || citedSources.length > 0) && <div className="qg-agent-workflow-citations">
        <strong>Supporting material</strong>
        {workflow.payload.course_chunk_ids.length > 0 && <small>{workflow.payload.course_chunk_ids.length} cited course passages</small>}
        {citedSources.map(source => <a key={source.id} href={source.url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a>)}
      </div>}
    </>}
    {workflow.status === "proposed" && <div className="qg-agent-workflow-actions">
      <button className="qg-paper-primary" type="button" disabled={busy} onClick={() => void onDecision(true)}>{busy ? <Loader2 size={15} className="qg-spin"/> : <Check size={15}/>} {assessment ? "Approve plan & review blueprint" : "Approve research output"}</button>
      <button type="button" disabled={busy} onClick={onRevise}><ArrowRight size={15}/> Request changes</button>
      <button type="button" disabled={busy} onClick={() => void onDecision(false)}><X size={15}/> Reject</button>
    </div>}
    {workflow.status === "rejected" && <small>Plan dismissed. Ask the agent for a different direction in the composer.</small>}
  </section>;
}
