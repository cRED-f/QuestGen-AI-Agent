"use client";

import {
  AlertCircle,
  ArrowUp,
  ArrowRight,
  ArrowUpRight,
  BookOpenText,
  ChevronRight,
  ArrowDown,
  ChevronDown,
  Activity,
  Files,
  Eye,
  EyeOff,
  ShieldCheck,
  Paperclip,
  PenLine,
  RefreshCw,
  PencilLine,
  Pencil,
  Check,
  Loader2,
  ClipboardList,
  Copy,
  RotateCcw,
  CheckCircle2,
  Menu,
  MessageSquareText,
  Plus,
  Settings2,
  Sun,
  Moon,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import FormattedMessage from "@/components/workspace/FormattedMessage";
import { writeMessageToClipboard } from "@/lib/clipboard";
import { DocumentPanel, type CourseMaterial, Source, sourceLink, sourceLocation } from "@/components/workspace/DocumentPanel";
import QuestionEditor, { type AssessmentQuestion } from "@/components/workspace/QuestionEditor";
import PencilSelect from "@/components/workspace/PencilSelect";
import ExamConfiguration, { type ExamSession, type ExamRequirements } from "@/components/workspace/ExamConfiguration";
import ExamPaperBuilder, { type ExamPaper, type PaperBlueprint, type QuestionEdits } from "@/components/workspace/ExamPaperBuilder";
import ExamPaperSidebar from "@/components/workspace/ExamPaperSidebar";

type Course = { id: string; name: string; created_at: number };
type Thread = { id: string; course_id: string; title: string; updated_at: number };
type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: number;
  mode?: "chat" | "exam";
  sources?: Source[];
  documents?: CourseMaterial[];
};
type AssessmentActivity = { id: number; stage: string; message: string; progress: number };
type AssessmentJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  prompt: string;
  blueprint: null | { title: string; instructions: string; total_marks: number; questions: unknown[] };
  questions: AssessmentQuestion[];
  document_ids?: string[];
  events: AssessmentActivity[];
  error: string | null;
};
type ModelSettings = {
  provider: "ollama" | "openrouter" | "openai" | "anthropic" | "9router";
  model: string;
};
type AvailableModel = { id: string; name: string };
type ProviderConnection = {
  provider: ModelSettings["provider"];
  key_configured: boolean;
  key_source: "saved" | "environment" | "missing";
  base_url: string | null;
  requires_key: boolean;
};
const CONNECTION_DEFAULTS: Record<"9router" | "ollama", string> = {
  "9router": "http://127.0.0.1:20128/v1",
  ollama: "http://127.0.0.1:11434",
};
const PROVIDER_KEY_LINKS: Partial<Record<ModelSettings["provider"], string>> = {
  "9router": "http://127.0.0.1:20128/dashboard",
  openai: "https://platform.openai.com/api-keys",
  anthropic: "https://console.anthropic.com/settings/keys",
  openrouter: "https://openrouter.ai/settings/keys",
};
const PROVIDERS: { value: ModelSettings["provider"]; label: string; detail: string }[] = [
  { value: "9router", label: "9Router", detail: "Local AI routing gateway" },
  { value: "openai", label: "OpenAI", detail: "OpenAI API key" },
  { value: "anthropic", label: "Anthropic", detail: "Claude API key" },
  { value: "openrouter", label: "OpenRouter", detail: "Multi-provider catalog" },
  { value: "ollama", label: "Ollama", detail: "Models installed locally" },
];
type ChatEvent = {
  type: "started" | "sources" | "delta" | "done" | "error";
  sources?: Source[];
  user_id?: string;
  message_id?: string;
  content?: string;
  message?: string;
};

const API = "/api/workspace";
// This is a per-conversation acknowledgement, never a global onboarding gate.
const examIntroKey = (id: string) => `questgen:exam-intro:${id}`;
const introAlreadyConfirmed = (id: string | null) =>
  Boolean(id && typeof window !== "undefined" && window.localStorage.getItem(examIntroKey(id)) === "true");
const SUGGESTIONS = [
  "Explain the difference between BFS and DFS with examples",
  "Help me understand a concept from my uploaded lectures",
  "Discuss how Bloom's taxonomy can improve learning",
];

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const fetchOnce = () => fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
    cache: "no-store",
  });
  let response: Response;
  try {
    response = await fetchOnce();
    // Reload may race a restarting local backend. Retry safe reads once only.
    if ((options?.method || "GET").toUpperCase() === "GET" && [500, 502, 503, 504].includes(response.status)) {
      await new Promise(resolve => setTimeout(resolve, 350));
      response = await fetchOnce();
    }
  } catch {
    throw new Error("Cannot reach QuestGen's local API. Check that the FastAPI backend is running on port 8000, then retry.");
  }
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    const message = typeof result.detail === "string" ? result.detail : "";
    const endpoint = path.split("?")[0].replace(/\/threads\/[^/]+/, "/threads/…");
    throw new Error(message || (response.status >= 500
      ? `Backend error (${response.status}) while loading ${endpoint}. Check the FastAPI terminal for the traceback.`
      : `Request failed (${response.status}) at ${endpoint}`));
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

async function readEvents(
  response: Response,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  if (!response.body) throw new Error("Streaming is unavailable in this browser.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  const dispatch = (block: string) => {
    const data = block.split("\n").filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart()).join("\n");
    if (!data) return;
    const event = JSON.parse(data) as ChatEvent;
    onEvent(event);
    if (event.type === "done") completed = true;
    if (event.type === "error") throw new Error(event.message || "Generation failed.");
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // The backend uses JSON SSE messages separated by blank lines.
      buffer = buffer.replace(/\r\n/g, "\n");
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        dispatch(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        end = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim()) dispatch(buffer);
    if (!completed) throw new Error("Connection ended before the reply was saved.");
  } finally {
    reader.releaseLock();
  }
}

export default function ChatPage() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseId, setCourseId] = useState("");
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [showScrollDown, setShowScrollDown] = useState(false);
  const [hitlActionsTarget, setHitlActionsTarget] = useState<HTMLDivElement | null>(null);
  const [activityExpanded, setActivityExpanded] = useState<Record<string, boolean>>({});
  const [assistantActivity, setAssistantActivity] = useState<Record<string, {started: number; visibleChars: number; done?: boolean}>>({});
  const [activityNow, setActivityNow] = useState(() => Date.now());

  const [workspaceMode, setWorkspaceMode] = useState<"chat" | "exam">("chat");
  const [examIntroConfirmed, setExamIntroConfirmed] = useState(false);
  const [examSession, setExamSession] = useState<ExamSession | null>(null);
  const [examBusy, setExamBusy] = useState(false);
  const [examPaper, setExamPaper] = useState<ExamPaper | null>(null);
  const [paperBusy, setPaperBusy] = useState(false);
  const paperAbortRef = useRef<AbortController | null>(null);
  const [assessment, setAssessment] = useState<AssessmentJob | null>(null);
  const [activities, setActivities] = useState<AssessmentActivity[]>([]);
  const [startingExam, setStartingExam] = useState(false);
  const [showAnswers, setShowAnswers] = useState(false);
  const [editingQuestionId, setEditingQuestionId] = useState<string | null>(null);
  const [settings, setSettings] = useState<ModelSettings>({ provider: "ollama", model: "" });
  const [settingsDraft, setSettingsDraft] = useState<ModelSettings>(settings);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [modelRefresh, setModelRefresh] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [connectionsLoading, setConnectionsLoading] = useState(false);
  const [providerKey, setProviderKey] = useState("");
  const [showProviderKey, setShowProviderKey] = useState(false);
  const [clearProviderKey, setClearProviderKey] = useState(false);
  const [providerBase, setProviderBase] = useState("");
  const [connectionFeedback, setConnectionFeedback] = useState("");
  const [connectionProblem, setConnectionProblem] = useState("");
  const [testingConnection, setTestingConnection] = useState(false);
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [documentMode, setDocumentMode] = useState<"library" | "picker">("library");
  const [libraryDocuments, setLibraryDocuments] = useState<CourseMaterial[]>([]);
  const [attachedDocuments, setAttachedDocuments] = useState<CourseMaterial[]>([]);
  const [courseOpen, setCourseOpen] = useState(false);
  const [courseName, setCourseName] = useState("");
  const [courseToDelete, setCourseToDelete] = useState<Course | null>(null);
  const [deleteConfirmName, setDeleteConfirmName] = useState("");
  const [courseDeleteError, setCourseDeleteError] = useState("");
  const [deletingCourse, setDeletingCourse] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [input, setInput] = useState("");
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [copyFeedback, setCopyFeedback] = useState<{ id: string; status: "copied" | "error" } | null>(null);
  const copyFeedbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copyAttemptRef = useRef(0);
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLElement | null>(null);
  const chatFrameRef = useRef<HTMLDivElement | null>(null);
  const followConversationRef = useRef(true);
  const scrollAnchorRef = useRef<{ id: string | null; top: number; scrollTop: number } | null>(null);
  const textRef = useRef<HTMLTextAreaElement | null>(null);
  const savedComposerDraftRef = useRef("");
  const chatDraftRef = useRef("");
  const examDraftRef = useRef("");
  const savedAttachmentsRef = useRef<CourseMaterial[]>([]);
  const activeCourseRef = useRef("");
  useEffect(() => {
    const saved = window.localStorage.getItem("questgen:theme");
    const initial = saved === "dark" ? "dark" : "light";
    setTheme(initial);
    document.documentElement.dataset.theme = initial;
    document.documentElement.style.colorScheme = initial;
  }, []);
  const chooseTheme = (next: "light" | "dark") => {
    setTheme(next);
    document.documentElement.dataset.theme = next;
    document.documentElement.style.colorScheme = next;
    window.localStorage.setItem("questgen:theme", next);
  };

  const currentCourse = courses.find((course) => course.id === courseId);
  const currentThread = threads.find((thread) => thread.id === threadId);

  useEffect(() => () => {
    if (copyFeedbackTimerRef.current !== null) clearTimeout(copyFeedbackTimerRef.current);
  }, []);

  const copyChatMessage = async (message: ChatMessage) => {
    if (!message.content.trim()) return;
    const attempt = ++copyAttemptRef.current;
    let status: "copied" | "error" = "copied";
    try {
      await writeMessageToClipboard(message.content);
    } catch {
      status = "error";
    }
    // Ignore an earlier copy attempt that resolves after the latest one.
    if (attempt !== copyAttemptRef.current) return;
    if (copyFeedbackTimerRef.current !== null) clearTimeout(copyFeedbackTimerRef.current);
    setCopyFeedback({ id: message.id, status });
    copyFeedbackTimerRef.current = setTimeout(() => setCopyFeedback(null), 2200);
  };

  const refreshLibrary = useCallback(async (id: string) => {
    const data = await requestJson<CourseMaterial[]>(`/courses/${encodeURIComponent(id)}/documents`);
    if (activeCourseRef.current !== id) return;
    setLibraryDocuments(data);
    // Deleted or no-longer-ready documents cannot be included in new requests.
    setAttachedDocuments((old) => old.filter((item) => data.some((doc) => doc.id === item.id && doc.status === "ready")));
  }, []);

  const openLibrary = (mode: "library" | "picker") => {
    setDocumentMode(mode);
    setDocumentsOpen(true);
    setSidebarOpen(false);
    if (courseId) void refreshLibrary(courseId).catch(() => {});
  };

  const refreshThreads = useCallback(async (id: string) => {
    const data = await requestJson<Thread[]>(`/threads?course_id=${encodeURIComponent(id)}`);
    setThreads(data);
    return data;
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([requestJson<Course[]>("/courses"), requestJson<ModelSettings>("/settings")])
      .then(([available, selected]) => {
        if (!active) return;
        setCourses(available);
        setSettings(selected);
        setSettingsDraft(selected);
        const last = window.localStorage.getItem("questgen:course");
        setCourseId(available.some((course) => course.id === last) ? last! : available[0]?.id || "");
      })
      .catch((cause: Error) => active && setError(cause.message))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setModelsLoading(true);
    setModelsError("");
    setAvailableModels([]);
    fetch(`${API}/models?provider=${encodeURIComponent(settings.provider)}`, { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not load provider models");
        return data as { models: AvailableModel[] };
      })
      .then((result) => setAvailableModels(result.models))
      .catch((cause: Error) => { if (!controller.signal.aborted) setModelsError(cause.message); })
      .finally(() => { if (!controller.signal.aborted) setModelsLoading(false); });
    return () => controller.abort();
  }, [settings.provider, modelRefresh]);

  const selectModel = async (model: string) => {
    if (busy || startingExam || model === settings.model) return;
    try {
      const updated = await requestJson<ModelSettings>("/settings", {
        method: "PUT", body: JSON.stringify({ provider: settings.provider, model }),
      });
      setSettings(updated);
      setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not change model."); }
  };

  useEffect(() => {
    if (!courseId) return;
    let active = true;
    activeCourseRef.current = courseId;
    window.localStorage.setItem("questgen:course", courseId);
    followConversationRef.current = true;
    setShowScrollDown(false);
    setWorkspaceMode(window.localStorage.getItem(`questgen:mode:${courseId}`) === "exam" ? "exam" : "chat");
    setExamIntroConfirmed(false);
    setThreads([]);
    setDocumentsOpen(false);
    setLibraryDocuments([]);
    setAttachedDocuments([]);
    savedAttachmentsRef.current = [];
    chatDraftRef.current = "";
    examDraftRef.current = "";
    setThreadId(null);
    setExamSession(null);
    setExamIntroConfirmed(false);
    setExamPaper(null);
    setMessages([]);
    setEditingMessageId(null);
    setInput("");
    savedComposerDraftRef.current = "";
    setAssessment(null);
    setActivities([]);
    void refreshLibrary(courseId).catch((cause: Error) => active && setError(cause.message));
    const libraryPoll = window.setInterval(() => void refreshLibrary(courseId).catch(() => {}), 4500);
    refreshThreads(courseId).then((available) => {
      if (!active) return;
      const last = window.localStorage.getItem(`questgen:thread:${courseId}`);
      if (last && available.some((item) => item.id === last)) {
        setThreadId(last);
        setExamIntroConfirmed(introAlreadyConfirmed(last));
        requestJson<ChatMessage[]>(`/threads/${last}/messages`)
          .then((items) => active && setMessages(items))
          .catch((cause: Error) => active && setError(cause.message));
      }
    }).catch((cause: Error) => active && setError(cause.message));
    return () => { active = false; window.clearInterval(libraryPoll); };
  }, [courseId, refreshThreads, refreshLibrary]);

  const refreshAssessment = useCallback(async (id: string) => {
    const jobs = await requestJson<AssessmentJob[]>(`/threads/${id}/generations`);
    setAssessment(jobs[0] || null);
    setActivities(jobs[0]?.events || []);
  }, []);

  useEffect(() => {
    // Legacy bulk-generation jobs are not part of the interactive Exam workflow.
    if (!threadId || workspaceMode === "exam") { setAssessment(null); setActivities([]); return; }
    let active = true;
    requestJson<AssessmentJob[]>(`/threads/${threadId}/generations`).then((jobs) => {
      if (!active) return;
      setAssessment(jobs[0] || null);
      setActivities(jobs[0]?.events || []);
    }).catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [threadId, workspaceMode]);

  useEffect(() => {
    if (!threadId || !assessment || (assessment.status !== "queued" && assessment.status !== "running")) return;
    const currentJob = assessment.id;
    const source = new EventSource(`${API}/threads/${threadId}/generations/${currentJob}/events`);
    const update = async () => {
      try {
        const job = await requestJson<AssessmentJob>(`/threads/${threadId}/generations/${currentJob}`);
        setAssessment((old) => old?.id === currentJob ? job : old);
        setActivities(job.events || []);
      } catch { /* Reconnect via the persisted SSE event stream. */ }
    };
    source.onmessage = (event) => {
      try {
        const info = JSON.parse(event.data) as AssessmentActivity & { type: string };
        if (info.type === "activity") {
          setActivities((old) => old.some((item) => item.id === info.id) ? old : [...old, info].slice(-35));
          if (["saved", "completed", "failed", "blueprint"].includes(info.stage)) void update();
        }
        if (info.type === "complete") { source.close(); void update(); }
      } catch { /* Ignore malformed partial events; a refresh reloads saved state. */ }
    };
    return () => source.close();
  }, [threadId, assessment?.id, assessment?.status]);

  // Keep the currently visible message stable when the mode (or composer) changes.
  // Do not remount the conversation or animate the entire message frame.
  const captureConversationAnchor = () => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    const firstVisible = Array.from(viewport.querySelectorAll<HTMLElement>("[data-message-id]"))
      .find(element => element.getBoundingClientRect().bottom > viewport.getBoundingClientRect().top);
    scrollAnchorRef.current = {
      id: firstVisible?.dataset.messageId ?? null,
      top: firstVisible?.getBoundingClientRect().top ?? 0,
      scrollTop: viewport.scrollTop,
    };
  };
  useLayoutEffect(() => {
    const saved = scrollAnchorRef.current;
    const viewport = scrollRef.current;
    if (!saved || !viewport) return;
    const anchor = Array.from(viewport.querySelectorAll<HTMLElement>("[data-message-id]"))
      .find(element => element.dataset.messageId === saved.id);
    viewport.scrollTop = anchor ? saved.scrollTop + anchor.getBoundingClientRect().top - saved.top : saved.scrollTop;
    scrollAnchorRef.current = null;
  }, [workspaceMode, examIntroConfirmed]);
  const updateScrollStatus = useCallback(() => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    const distance = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    followConversationRef.current = distance < 95;
    setShowScrollDown(distance > 145);
  }, []);
  const scrollToLatest = useCallback((smooth = true) => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    followConversationRef.current = true;
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    viewport.scrollTo({top: viewport.scrollHeight, behavior: smooth && !reducedMotion ? "smooth" : "auto"});
    setShowScrollDown(false);
  }, []);
  useLayoutEffect(() => {
    if (followConversationRef.current && !scrollAnchorRef.current) scrollToLatest(false);
  }, [messages, busy, scrollToLatest]);
  useEffect(() => {
    const frame = chatFrameRef.current;
    if (!frame || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (followConversationRef.current && !scrollAnchorRef.current) scrollToLatest(false);
      else updateScrollStatus();
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, [workspaceMode, threadId, scrollToLatest, updateScrollStatus]);
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => setActivityNow(Date.now()), 600);
    return () => window.clearInterval(timer);
  }, [busy]);

  // Stay one line when empty, but grow naturally for longer prompts and edits.
  useEffect(() => {
    const textarea = textRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, Math.min(142, window.innerHeight * 0.25))}px`;
  }, [input]);

  useEffect(() => {
    if (workspaceMode === "exam" && examIntroConfirmed) textRef.current?.focus();
  }, [workspaceMode, examIntroConfirmed]);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    // Editing uses the regular chat composer rather than an inline message form.
    if (!editingMessageId) return;
    const textarea = textRef.current;
    if (!textarea) return;
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  }, [editingMessageId]);

  const beginNewChat = () => {
    if (busy) return;
    setExamIntroConfirmed(false);
    setThreadId(null);
    setExamSession(null);
    setExamPaper(null);
    setMessages([]);
    setEditingMessageId(null);
    setInput((current) => editingMessageId ? savedComposerDraftRef.current : current);
    savedComposerDraftRef.current = "";
    setAssessment(null);
    setActivities([]);
    setAttachedDocuments([]);
    savedAttachmentsRef.current = [];
    setError("");
    setSidebarOpen(false);
    if (courseId) window.localStorage.removeItem(`questgen:thread:${courseId}`);
    textRef.current?.focus();
  };

  const openThread = async (id: string) => {
    if (busy || id === threadId) return;
    setExamIntroConfirmed(introAlreadyConfirmed(id));
    followConversationRef.current = true;
    setShowScrollDown(false);
    setError("");
    setThreadId(id);
    setExamSession(null);
    setExamPaper(null);
    setEditingMessageId(null);
    if (editingMessageId) setInput(savedComposerDraftRef.current);
    savedComposerDraftRef.current = "";
    setAssessment(null);
    setActivities([]);
    setAttachedDocuments([]);
    savedAttachmentsRef.current = [];
    setSidebarOpen(false);
    window.localStorage.setItem(`questgen:thread:${courseId}`, id);
    try {
      setMessages(await requestJson<ChatMessage[]>(`/threads/${id}/messages`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load conversation.");
    }
  };

  // Morph only the composer/confirmation surface. The View Transitions API
  // interpolates their actual bounds; unsupported browsers update immediately.
  // No animation runs for users who prefer reduced motion.
  const morphComposer = (update: () => void) => {
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    const viewDocument = document as Document & {
      startViewTransition?: (callback: () => void) => { finished: Promise<void> };
    };
    captureConversationAnchor();
    if (reducedMotion || typeof viewDocument.startViewTransition !== "function") {
      update();
      return;
    }
    const transition = viewDocument.startViewTransition(() => flushSync(update));
    void transition.finished.catch(() => {});
  };

  // The selected mode belongs to the workspace, not a new conversation.
  const switchMode = async (mode: "chat" | "exam") => {
    if (busy || examBusy || startingExam || editingMessageId || mode === workspaceMode) return;
    if (workspaceMode === "chat") chatDraftRef.current = input;
    else examDraftRef.current = input;
    const nextConfirmed = mode === "exam" ? introAlreadyConfirmed(threadId) : examIntroConfirmed;
    const currentHasIntro = workspaceMode === "exam" && !examIntroConfirmed;
    const nextHasIntro = mode === "exam" && !nextConfirmed;
    const updateMode = () => {
      setInput(mode === "exam" ? examDraftRef.current : chatDraftRef.current);
      setExamIntroConfirmed(nextConfirmed);
      setWorkspaceMode(mode);
      setError("");
    };
    if (currentHasIntro !== nextHasIntro) {
      morphComposer(updateMode);
    } else {
      // Already onboarded: preserve the live composer and all chat DOM nodes.
      captureConversationAnchor();
      flushSync(updateMode);
    }
    if (courseId) window.localStorage.setItem(`questgen:mode:${courseId}`, mode);
    if (mode !== "exam" || !courseId) return;
    setExamBusy(true);
    try {
      let target = threadId;
      if (!target) {
        const created = await requestJson<Thread>("/threads", {
          method: "POST", body: JSON.stringify({ course_id: courseId }),
        });
        target = created.id;
        setThreadId(target);
        setExamIntroConfirmed(introAlreadyConfirmed(target));
        setThreads(old => [created, ...old]);
        window.localStorage.setItem(`questgen:thread:${courseId}`, target);
      }
      const session = await requestJson<ExamSession>(`/threads/${target}/exam/start`, { method: "POST" });
      setExamSession(session);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not open Exam Mode.");
    } finally { setExamBusy(false); }
  };

  const refreshExamPaper = useCallback(async (id: string) => {
    const paper = await requestJson<ExamPaper | null>(`/threads/${id}/exam/paper`);
    setExamPaper(paper);
    return paper;
  }, []);

  useEffect(() => {
    if (!threadId || workspaceMode !== "exam") { setExamPaper(null); return; }
    let active = true;
    requestJson<ExamPaper | null>(`/threads/${threadId}/exam/paper`)
      .then(paper => { if (active) setExamPaper(paper); })
      .catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [threadId, workspaceMode]);

  const paperAction = async (path: string, method: string = "POST", body?: unknown) => {
    if (!threadId || paperBusy || examBusy) return;
    setPaperBusy(true);
    setError("");
    const generating = path === "/next" || path === "/regenerate";
    const abort = generating ? new AbortController() : null;
    if (generating) {
      paperAbortRef.current = abort;
      setExamPaper(old => old ? {...old, status: "generating"} : old);
    }
    try {
      const paper = await requestJson<ExamPaper>(`/threads/${threadId}/exam/paper${path}`, {
        method, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        ...(abort ? {signal: abort.signal} : {}),
      });
      setExamPaper(paper);
    } catch (cause) {
      if (!(abort?.signal.aborted)) setError(cause instanceof Error ? cause.message : "Could not update exam paper.");
      // A timed-out request may have completed server-side; read the authoritative state.
      await refreshExamPaper(threadId).catch(() => {});
    } finally {
      if (paperAbortRef.current === abort) paperAbortRef.current = null;
      setPaperBusy(false);
    }
  };

  const cancelPaperGeneration = async () => {
    if (!threadId) return;
    paperAbortRef.current?.abort();
    setError("");
    try {
      const paper = await requestJson<ExamPaper>(`/threads/${threadId}/exam/paper/cancel`, {method: "POST"});
      setExamPaper(paper);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not cancel generation.");
      await refreshExamPaper(threadId).catch(() => {});
    }
  };

  const downloadPaper = async (audience: "student" | "instructor", format: "pdf" | "docx") => {
    if (!threadId || !examPaper?.final_approved) return;
    setError("");
    try {
      const response = await fetch(`${API}/threads/${threadId}/exam/paper/export/${audience}.${format}`, {cache: "no-store"});
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || `Download failed (${response.status})`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `questgen-paper-${audience}-${threadId.slice(0,8)}.${format}`;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not download the paper.");
      await refreshExamPaper(threadId).catch(() => {});
    }
  };

  useEffect(() => {
    if (!threadId || workspaceMode !== "exam") return;
    let active = true;
    requestJson<ExamSession>(`/threads/${threadId}/exam/start`, { method: "POST" })
      .then(session => { if (active) setExamSession(session); })
      .catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [threadId, workspaceMode]);

  const reconnectWorkspace = async () => {
    if (!threadId) { setError(""); return; }
    setError("");
    try {
      const saved = await requestJson<ChatMessage[]>(`/threads/${threadId}/messages`);
      setMessages(saved);
      if (workspaceMode === "exam") {
        const current = await requestJson<ExamSession>(`/threads/${threadId}/exam`);
        const session = current.status === "not_started"
          ? await requestJson<ExamSession>(`/threads/${threadId}/exam/start`, {method: "POST"}) : current;
        setExamSession(session);
        await refreshExamPaper(threadId);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not reconnect to the workspace.");
    }
  };

  const saveExamRequirements = async (updates: Partial<ExamRequirements>, documentIds?: string[]) => {
    if (!threadId || examBusy) return;
    setExamBusy(true);
    setError("");
    try {
      const session = await requestJson<ExamSession>(`/threads/${threadId}/exam/requirements`, {
        method: "PATCH", body: JSON.stringify({ updates, ...(documentIds ? { document_ids: documentIds } : {}) }),
      });
      setExamSession(session);
      setExamPaper(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not save exam details."); }
    finally { setExamBusy(false); }
  };

  const submitExamQuickReply = async (updates: Partial<ExamRequirements>, answer: string, documentIds?: string[]) => {
    if (!threadId || examBusy || busy || !examIntroConfirmed) return false;
    setExamBusy(true);
    setError("");
    try {
      const result = await requestJson<{user_id: string; message_id: string; reply: string; session: ExamSession}>(
        `/threads/${threadId}/exam/quick-reply`, {
          method: "POST",
          body: JSON.stringify({updates, answer, ...(documentIds ? {document_ids: documentIds} : {})}),
        });
      setExamSession(result.session);
      setExamPaper(null);
      // The requirement save succeeded. A secondary history/sidebar refresh must
      // not turn that success into a reported failure or discard the editor state.
      try {
        const saved = await requestJson<ChatMessage[]>(`/threads/${threadId}/messages`);
        setMessages(saved);
      } catch { /* Messages will be reloaded on the next conversation refresh. */ }
      void refreshThreads(courseId).catch(() => {});
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save that exam answer.");
      return false;
    } finally {
      setExamBusy(false);
    }
  };

  const approveExam = async () => {
    if (!threadId || examBusy) return;
    setExamBusy(true);
    setError("");
    try {
      setExamSession(await requestJson<ExamSession>(`/threads/${threadId}/exam/approve`, { method: "POST" }));
      await refreshExamPaper(threadId);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not approve configuration."); }
    finally { setExamBusy(false); }
  };

  const deleteThread = async (id: string) => {
    if (busy || deletingThreadId) return;
    setDeletingThreadId(id);
    try {
      await requestJson<void>(`/threads/${id}`, { method: "DELETE" });
      setThreads((previous) => previous.filter((item) => item.id !== id));
      if (threadId === id) beginNewChat();
      await refreshThreads(courseId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete conversation.");
    } finally { setDeletingThreadId(null); }
  };

  const addCourse = async (event: FormEvent) => {
    event.preventDefault();
    if (!courseName.trim() || busy) return;
    setSaving(true);
    try {
      const created = await requestJson<Course>("/courses", {
        method: "POST", body: JSON.stringify({ name: courseName.trim() }),
      });
      setCourses((previous) => [...previous, created]);
      setCourseId(created.id);
      setCourseName("");
      setCourseOpen(false);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create course.");
    } finally {
      setSaving(false);
    }
  };

  const confirmDeleteCourse = async () => {
    if (!courseToDelete || deleteConfirmName !== courseToDelete.name || deletingCourse || busy) return;
    const target = courseToDelete;
    setDeletingCourse(true);
    setCourseDeleteError("");
    try {
      await requestJson<void>(`/courses/${encodeURIComponent(target.id)}`, { method: "DELETE" });
      window.localStorage.removeItem(`questgen:thread:${target.id}`);
      const remaining = courses.filter((item) => item.id !== target.id);
      setCourses(remaining);
      if (courseId === target.id) {
        const nextId = remaining[0]?.id || "";
        activeCourseRef.current = nextId;
        setCourseId(nextId);
        setThreadId(null);
        setThreads([]);
        setMessages([]);
        setLibraryDocuments([]);
        setAttachedDocuments([]);
        savedAttachmentsRef.current = [];
        setAssessment(null);
        setActivities([]);
        setEditingMessageId(null);
        setInput("");
        setDocumentsOpen(false);
        if (!nextId) window.localStorage.removeItem("questgen:course");
        else window.localStorage.setItem("questgen:course", nextId);
      }
      setCourseToDelete(null);
      setDeleteConfirmName("");
      setError("");
    } catch (cause) {
      setCourseDeleteError(cause instanceof Error ? cause.message : "Could not delete the course.");
    } finally {
      setDeletingCourse(false);
    }
  };

  const selectedConnection = connections.find((entry) => entry.provider === settingsDraft.provider);
  const localProvider = settingsDraft.provider === "9router" || settingsDraft.provider === "ollama";

  const openSettings = async () => {
    setSettingsDraft(settings);
    setSettingsOpen(true);
    setConnectionsLoading(true);
    setProviderKey("");
    setShowProviderKey(false);
    setClearProviderKey(false);
    setConnectionFeedback("");
    setConnectionProblem("");
    try {
      const loaded = await requestJson<ProviderConnection[]>("/provider-connections");
      setConnections(loaded);
      setProviderBase(loaded.find((entry) => entry.provider === settings.provider)?.base_url || "");
    } catch (cause) {
      setConnectionProblem(cause instanceof Error ? cause.message : "Could not load provider connections.");
    } finally { setConnectionsLoading(false); }
  };

  const changeProviderDraft = (provider: ModelSettings["provider"]) => {
    setSettingsDraft((previous) => ({ ...previous, provider }));
    setProviderKey("");
    setShowProviderKey(false);
    setClearProviderKey(false);
    setConnectionFeedback("");
    setConnectionProblem("");
    setProviderBase(connections.find((entry) => entry.provider === provider)?.base_url ||
      (provider === "9router" || provider === "ollama" ? CONNECTION_DEFAULTS[provider] : ""));
  };

  const persistProviderConnection = async () => {
    const updated = await requestJson<ProviderConnection>(`/provider-connections/${settingsDraft.provider}`, {
      method: "PUT",
      body: JSON.stringify({
        ...(providerKey.trim() ? { api_key: providerKey.trim() } : {}),
        clear_key: clearProviderKey,
        ...(localProvider ? { base_url: providerBase.trim() } : {}),
      }),
    });
    setConnections((previous) => [...previous.filter((entry) => entry.provider !== updated.provider), updated]);
    setProviderKey("");
    setClearProviderKey(false);
    return updated;
  };

  const testProvider = async () => {
    setTestingConnection(true);
    setConnectionProblem("");
    setConnectionFeedback("");
    try {
      await persistProviderConnection();
      const response = await requestJson<{ connected: boolean; models_found: number }>(`/provider-connections/${settingsDraft.provider}/test`, {
        method: "POST",
      });
      setConnectionFeedback(`Connection successful · ${response.models_found} models found. ${settingsDraft.provider === settings.provider ? "Choose one in chat." : "Save provider to use it in chat."}`);
      if (settingsDraft.provider === settings.provider) setModelRefresh((previous) => previous + 1);
    } catch (cause) {
      setConnectionProblem(cause instanceof Error ? cause.message : "Could not connect to this provider.");
    } finally { setTestingConnection(false); }
  };

  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setConnectionProblem("");
    try {
      await persistProviderConnection();
      const updated = await requestJson<ModelSettings>("/settings", {
        method: "PUT", body: JSON.stringify({ provider: settingsDraft.provider, model: settingsDraft.provider === settings.provider ? settings.model : "" }),
      });
      setSettings(updated);
      setSettingsDraft(updated);
      setModelRefresh((previous) => previous + 1);
      setSettingsOpen(false);
      setError("");
    } catch (cause) {
      setConnectionProblem(cause instanceof Error ? cause.message : "Could not save model settings.");
    } finally {
      setSaving(false);
    }
  };

  // Editing a user turn or regenerating a reply replaces its later messages.
  // Persisted message IDs are used so the backend can update the correct turn.
  const send = async (
    value = input,
    action?: { edit_message_id?: string; regenerate_message_id?: string },
  ) => {
    const content = value.trim();
    if ((!content && !action?.regenerate_message_id) || !courseId || busy || examBusy || startingExam ||
        (editingMessageId !== null && action?.edit_message_id !== editingMessageId)) return;
    if (action && (!threadId || workspaceMode === "exam")) return;
    const existingId = threadId;
    setBusy(true);
    setError("");
    if (!action) setInput("");
    const chosenDocuments = action?.regenerate_message_id ? [] : attachedDocuments;
    let editWasAccepted = false;
    const controller = new AbortController();
    abortRef.current = controller;
    let activeId = existingId;
    const timestamp = Date.now();
    followConversationRef.current = true;
    setShowScrollDown(false);
    const user: ChatMessage = {
      id: `pending-user-${timestamp}`, role: "user", content, created_at: timestamp / 1000,
      documents: chosenDocuments, mode: workspaceMode,
    };
    const answer: ChatMessage = {
      id: `pending-answer-${timestamp}`, role: "assistant", content: "", created_at: timestamp / 1000, mode: workspaceMode,
    };
    setAssistantActivity(previous => ({...previous, [answer.id]: {started: timestamp, visibleChars: 0}}));
    setActivityExpanded(previous => ({...previous, [answer.id]: true}));
    setMessages((previous) => {
      if (action?.edit_message_id) {
        const index = previous.findIndex((item) => item.id === action.edit_message_id);
        if (index < 0) return previous;
        return [...previous.slice(0, index), { ...previous[index], content, documents: chosenDocuments }, answer];
      }
      if (action?.regenerate_message_id) {
        const index = previous.findIndex((item) => item.id === action.regenerate_message_id);
        if (index < 0) return previous;
        return [...previous.slice(0, index), answer];
      }
      return [...previous, user, answer];
    });
    try {
      if (!activeId) {
        const created = await requestJson<Thread>("/threads", {
          method: "POST", body: JSON.stringify({ course_id: courseId }),
        });
        activeId = created.id;
        setThreadId(created.id);
        window.localStorage.setItem(`questgen:thread:${courseId}`, created.id);
        setThreads((previous) => [created, ...previous]);
      }
      if (controller.signal.aborted) throw new Error("Response stopped.");
      if (workspaceMode === "exam") {
        const result = await requestJson<{user_id: string; message_id: string; reply: string; session: ExamSession; sources: Source[]; paper?: ExamPaper | null}>(
          `/threads/${activeId}/exam/turn`, {
            method: "POST", signal: controller.signal,
            body: JSON.stringify({ content, ...(chosenDocuments.length ? {document_ids: chosenDocuments.map(doc => doc.id)} : {}) }),
          });
        setExamSession(result.session);
        setAssistantActivity(previous => ({...previous,
          [result.message_id]: {started: timestamp, visibleChars: result.reply.length, done: true}}));
        setActivityExpanded(previous => ({...previous, [result.message_id]: true}));
        if ("paper" in result) setExamPaper(result.paper ?? null);
        else if (result.session.status !== "approved") setExamPaper(null);
        setMessages(previous => previous.map(item => item.id === user.id ? {...item, id: result.user_id}
          : item.id === answer.id ? {...item, id: result.message_id, content: result.reply, sources: result.sources} : item));
      } else {
      const response = await fetch(`${API}/threads/${activeId}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(action?.regenerate_message_id ? {} : { content }),
          ...action,
          ...(!action?.regenerate_message_id ? { document_ids: chosenDocuments.map((doc) => doc.id) } : {}),
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `Could not send message (${response.status})`);
      }
      // The server has accepted the edit and updated the saved conversation.
      // Even if the provider subsequently fails, this edit no longer exists as a pending request.
      if (action?.edit_message_id) editWasAccepted = true;
      await readEvents(response, (event) => {
        if (event.type === "started" && event.user_id) {
          setMessages((previous) => previous.map((item) => item.id === user.id ? { ...item, id: event.user_id! } : item));
        }
        if (event.type === "sources") {
          setMessages((previous) => previous.map((item) => item.id === answer.id
            ? { ...item, sources: event.sources || [] } : item));
        }
        if (event.type === "delta") {
          setAssistantActivity(previous => ({...previous, [answer.id]: {
            started: timestamp, visibleChars: (previous[answer.id]?.visibleChars || 0) + (event.content || "").length,
          }}));
          setMessages((previous) => previous.map((item) => item.id === answer.id
            ? { ...item, content: item.content + (event.content || "") } : item));
        }
        if (event.type === "done" && event.message_id) {
          setAssistantActivity(previous => ({...previous, [event.message_id!]: {
            started: timestamp, visibleChars: previous[answer.id]?.visibleChars || 0, done: true,
          }}));
          setActivityExpanded(previous => ({...previous, [event.message_id!]: previous[answer.id] !== false}));
          setMessages((previous) => previous.map((item) => item.id === answer.id ? { ...item, id: event.message_id! } : item));
        }
      });
      }
    } catch (cause) {
      setAssistantActivity(previous => ({...previous, [answer.id]: {
        started: timestamp, visibleChars: previous[answer.id]?.visibleChars || 0, done: true,
      }}));
      setError(controller.signal.aborted ? "Response stopped. Incomplete responses are not saved." :
        (cause instanceof Error ? cause.message : "Could not generate a response."));
    } finally {
      abortRef.current = null;
      setBusy(false);
      // Preserve chosen source files for follow-up turns in this conversation.
      // Without a selection, the backend automatically uses the ready course library.
      if (editWasAccepted) {
        setEditingMessageId(null);
        setInput(savedComposerDraftRef.current);
        savedComposerDraftRef.current = "";
        setAttachedDocuments(savedAttachmentsRef.current);
        savedAttachmentsRef.current = [];
      }
      if (activeId) {
        try {
          const [saved] = await Promise.all([
            requestJson<ChatMessage[]>(`/threads/${activeId}/messages`),
            refreshThreads(courseId),
          ]);
          setMessages(saved);
        } catch { /* Keep local content and display the preceding error if any. */ }
      } else {
        setMessages((previous) => previous.filter((item) => item.id !== answer.id));
      }
      textRef.current?.focus();
    }
  };

  const editChatMessage = (message: ChatMessage) => {
    if (busy || startingExam || message.id.startsWith("pending-")) return;
    // Keep the unsent draft so Cancel (or a completed edit) can restore it.
    if (!editingMessageId) {
      savedComposerDraftRef.current = input;
      savedAttachmentsRef.current = attachedDocuments;
    }
    setAttachedDocuments(message.documents || []);
    setEditingMessageId(message.id);
    setInput(message.content);
    setError("");
  };

  const cancelMessageEdit = () => {
    if (!editingMessageId || busy) return;
    setEditingMessageId(null);
    setInput(savedComposerDraftRef.current);
    savedComposerDraftRef.current = "";
    setAttachedDocuments(savedAttachmentsRef.current);
    savedAttachmentsRef.current = [];
    setError("");
    textRef.current?.focus();
  };

  const startAssessment = async () => {
    const prompt = input.trim();
    if (!prompt || !courseId || busy || startingExam || assessment?.status === "running" || assessment?.status === "queued") return;
    setStartingExam(true);
    setError("");
    try {
      let target = threadId;
      if (!target) {
        const created = await requestJson<Thread>("/threads", {
          method: "POST", body: JSON.stringify({ course_id: courseId }),
        });
        target = created.id;
        setThreadId(target);
        setThreads((items) => [created, ...items]);
        window.localStorage.setItem(`questgen:thread:${courseId}`, target);
      }
      const job = await requestJson<AssessmentJob>(`/threads/${target}/generations`, {
        method: "POST", body: JSON.stringify({ prompt, document_ids: attachedDocuments.map((doc) => doc.id) }),
      });
      setAssessment(job);
      setActivities(job.events || []);
      setInput("");
      // Keep the document scope when the professor follows up on this exam.
      setShowAnswers(false);
      await refreshThreads(courseId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start assessment.");
    } finally {
      setStartingExam(false);
    }
  };

  const questionAction = async (id: string, path: string, method: "PUT" | "POST", body: object) => {
    if (!assessment || !threadId || assessment.status !== "completed") return;
    setEditingQuestionId(id);
    setError("");
    try {
      const base = `/threads/${threadId}/generations/${assessment.id}`;
      await requestJson(`${base}/questions/${id}${path ? `/${path}` : ""}`, {
        method, body: JSON.stringify(body),
      });
      const updated = await requestJson<AssessmentJob>(base);
      setAssessment(updated);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update question.");
      throw cause;
    } finally { setEditingQuestionId(null); }
  };

  const questionHistory = async (id: string) => {
    if (!assessment || !threadId) throw new Error("No active assessment");
    return requestJson<{versions: {version:number;content: AssessmentQuestion["content"];change_type:string;instruction:string;created_at:number}[]}>(
      `/threads/${threadId}/generations/${assessment.id}/questions/${id}/versions`);
  };

  const customizeAssessment = async () => {
    if (!assessment || !threadId || assessment.status !== "completed" || !input.trim() || busy || startingExam) return;
    setStartingExam(true);
    setError("");
    try {
      await requestJson(`/threads/${threadId}/generations/${assessment.id}/customize`, {
        method: "POST", body: JSON.stringify({ instruction: input.trim() }),
      });
      setInput("");
      await Promise.all([refreshAssessment(threadId), requestJson<ChatMessage[]>(`/threads/${threadId}/messages`).then(setMessages)]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not apply requested changes."); }
    finally { setStartingExam(false); }
  };

  const downloadExam = async (audience: "student" | "instructor", format: "pdf" | "docx") => {
    if (!assessment || !threadId) return;
    try {
      const url = `${API}/threads/${threadId}/generations/${assessment.id}/export/${audience}.${format}`;
      const response = await fetch(url);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Export failed (${response.status})`);
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `questgen-${audience}-${assessment.id.slice(0,8)}.${format}`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not export assessment."); }
  };

  const retryAssessment = async () => {
    if (!assessment || !threadId || assessment.status !== "failed") return;
    setStartingExam(true);
    try {
      const job = await requestJson<AssessmentJob>(`/threads/${threadId}/generations/${assessment.id}/retry`, { method: "POST" });
      setAssessment(job);
      setActivities(job.events || []);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not retry assessment.");
    } finally { setStartingExam(false); }
  };

  const sendFromComposer = () => {
    void send(input, editingMessageId ? { edit_message_id: editingMessageId } : undefined);
  };
  const onSubmit = (event: FormEvent) => { event.preventDefault(); sendFromComposer(); };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Escape" && editingMessageId) {
      event.preventDefault();
      cancelMessageEdit();
    } else if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      sendFromComposer();
    }
  };

  return (
    <div className="qg-app" data-theme={theme}>
      {sidebarOpen && (
        <button type="button" aria-label="Close navigation" className="qg-mobile-scrim" onClick={() => setSidebarOpen(false)} />
      )}
      <aside className={`qg-sidebar ${sidebarOpen ? "qg-sidebar--open" : ""}`} aria-label="Workspace navigation">
        <div className="qg-brand">
          <div className="qg-brand-mark" aria-hidden="true"><img src="/icon.svg" alt="" width={39} height={39}/></div>
          <div className="qg-brand-copy"><strong>QuestGen<span className="qg-brand-period">.</span></strong><span>THE ASSESSMENT STUDIO</span></div>
          <button type="button" aria-label="Close navigation" className="qg-icon-button qg-sidebar-close" onClick={() => setSidebarOpen(false)}><X size={19}/></button>
        </div>

        <div className="qg-sidebar-body">
          <button type="button" className="qg-new-chat" onClick={beginNewChat} disabled={busy}>
            <span className="qg-new-chat-icon"><Plus size={18} strokeWidth={2}/></span>
            <span>New conversation</span>
            <ArrowUpRight size={15} className="qg-new-chat-arrow" />
          </button>

          <div className="qg-side-section">
            <div className="qg-section-header">
              <span className="qg-overline">01 / YOUR COURSE</span>
              <button type="button" onClick={() => setCourseOpen(true)} className="qg-mini-icon" aria-label="Create course" title="Create course"><Plus size={17}/></button>
            </div>
            <PencilSelect ariaLabel="Your course" placement="sidebar" value={courseId} disabled={busy}
              options={courses.map((course) => ({ value: course.id, label: course.name }))}
              onChange={setCourseId} placeholder="Choose a course"
              onDeleteOption={(id) => { setCourseToDelete(courses.find((item) => item.id === id) || null); setDeleteConfirmName(""); setCourseDeleteError(""); setSidebarOpen(false); }}
              deletingOption={deletingCourse ? courseToDelete?.id : null}/>
          </div>

          <div className="qg-side-section qg-library-section">
            <div className="qg-section-header"><span className="qg-overline">02 / LIBRARY</span><span className="qg-count">{libraryDocuments.length}</span></div>
            <button type="button" className="qg-library-link" onClick={() => openLibrary("library")} disabled={!courseId}>
              <Files size={18} strokeWidth={1.8}/><span>Manage {currentCourse?.name || "course"} library</span><ArrowUpRight size={15} className="qg-link-tail"/>
            </button>
            <div className="qg-library-note">Files remain in their own course space.</div>
            <div className="qg-library-files" aria-label="Files in selected course">
              {libraryDocuments.length === 0 && <span className="qg-library-empty">No materials yet</span>}
              {libraryDocuments.map((doc) => (
                <button type="button" className="qg-library-file" key={doc.id} title={doc.filename} onClick={() => openLibrary("library")}>
                  <Files size={14} aria-hidden="true"/><span>{doc.filename}</span>
                  {doc.status !== "ready" && <span className="qg-library-status" title={doc.status}>·</span>}
                </button>
              ))}
            </div>
          </div>

          <div className="qg-side-section qg-history-section">
            <div className="qg-section-header"><span className="qg-overline">03 / CONVERSATIONS</span><span className="qg-count">{threads.length}</span></div>
            <nav aria-label="Recent conversations" className="qg-history-list">
              {threads.length === 0 && <p className="qg-empty-history">Your recent conversations will appear here.</p>}
              {threads.map((thread) => (
                <div key={thread.id} className={`qg-history-row ${threadId === thread.id ? "qg-history-row--active" : ""}`}>
                  <button type="button" className="qg-history-select" onClick={() => void openThread(thread.id)} disabled={busy} title={thread.title}>
                    <MessageSquareText size={16} strokeWidth={1.65}/><span>{thread.title}</span>
                  </button>
                  <button type="button" className="qg-history-delete" onClick={() => void deleteThread(thread.id)} disabled={busy || deletingThreadId !== null} aria-label={`Delete ${thread.title}`} title="Delete conversation"><Trash2 size={15}/></button>
                </div>
              ))}
            </nav>
          </div>
        </div>

        <div className="qg-sidebar-foot">
          <button type="button" className="qg-settings-link" onClick={() => void openSettings()}>
            <span className="qg-settings-icon"><Settings2 size={19}/></span>
            <span className="qg-settings-copy"><strong>Model settings</strong></span>
            <ChevronRight size={17} aria-hidden="true"/>
          </button>
          <div className="qg-local-label"><span className="qg-status-dot"/> LOCAL WORKSPACE <span className="qg-local-version">V2.0</span></div>
        </div>
      </aside>

      <main className="qg-main">
        <header className="qg-topbar">
          <div className="qg-topbar-left">
            <button type="button" aria-label="Open navigation" className="qg-icon-button qg-menu-button" onClick={() => setSidebarOpen(true)}><Menu size={21}/></button>
            <span className="qg-breadcrumb-root">Workspace</span><ChevronRight size={14} className="qg-breadcrumb-chevron"/>
            <span className="qg-breadcrumb-current">{currentCourse?.name || "Choose a course"}</span>
          </div>
          <div className="qg-mode-switch" data-mode={workspaceMode} role="group" aria-label="Workspace mode">
            <span className="qg-mode-switch-indicator" aria-hidden="true"/>
            <button type="button" aria-pressed={workspaceMode === "chat"} onClick={() => void switchMode("chat")} disabled={busy || examBusy}><MessageSquareText size={15}/> Chat</button>
            <button type="button" aria-pressed={workspaceMode === "exam"} onClick={() => void switchMode("exam")} disabled={busy || examBusy}><ClipboardList size={15}/> Exam</button>
          </div>
          <div className="qg-topbar-right">
            <span className="qg-current-label" title={currentThread?.title || "New conversation"}>{currentThread?.title || "Untitled conversation"}</span>
            <button type="button" className="qg-topbar-button" onClick={() => openLibrary("picker")} disabled={!courseId}><Files size={17}/><span>Materials</span></button>
            <PencilSelect ariaLabel={`${PROVIDERS.find((item) => item.value === settings.provider)?.label || "Provider"} models`} placement="topbar" value={settings.model}
              placeholder="Choose a model" disabled={busy || startingExam}
              options={availableModels.map((item) => ({ value: item.id, label: item.name, detail: item.name === item.id ? undefined : item.id }))}
              searchable loading={modelsLoading} error={modelsError}
              onRefresh={() => setModelRefresh((previous) => previous + 1)} onChange={(id) => void selectModel(id)}/>
          </div>
        </header>

        <div className={`qg-paper-workspace ${workspaceMode === "exam" && examIntroConfirmed && examSession?.status === "approved" ? "qg-paper-workspace--building" : ""}`}>
          <div className={`qg-chat-column ${workspaceMode === "exam" ? "qg-chat-column--exam" : ""}`}>
          <section ref={scrollRef} onScroll={updateScrollStatus} className={`qg-chat-scroll ${!loading && workspaceMode === "chat" && messages.length === 0 && !assessment ? "qg-chat-scroll--welcome" : ""}`} aria-live="polite" aria-label="Conversation">
            <div className="qg-chat-frame" ref={chatFrameRef}>
              {loading ? (
                <div className="qg-loading"><Loader2 size={20} className="qg-spin"/><span>Opening your workspace…</span></div>
              ) : messages.length === 0 && workspaceMode === "chat" ? (
                <div className="qg-welcome">
                  <div className="qg-welcome-issue"><span className="qg-issue-line"/> THE PROFESSOR'S WORKSPACE <span className="qg-issue-line"/></div>
                  <div className="qg-welcome-icon" aria-hidden="true"><PenLine size={27} strokeWidth={1.5}/><span className="qg-icon-spark">✳</span></div>
                  <h1>Good questions<br/>start <em>here.</em></h1>
                  <p className="qg-welcome-subtitle">A thoughtful space to plan assessments, explore course material, and refine every question.</p>
                  <div className="qg-suggestions-title"><span className="qg-overline">A FEW WAYS TO BEGIN</span><span className="qg-title-rule"/></div>
                  <div className="qg-suggestion-grid">
                    {SUGGESTIONS.map((suggestion, index) => (
                      <button type="button" className="qg-suggestion" key={suggestion} disabled={busy || !courseId} onClick={() => void send(suggestion)}>
                        <span className="qg-suggestion-num">0{index + 1}</span><span className="qg-suggestion-body">{suggestion}</span><ArrowUpRight size={18} className="qg-suggestion-arrow"/>
                      </button>
                    ))}
                  </div>
                  <button type="button" onClick={() => openLibrary("picker")} className="qg-source-hint" disabled={!courseId}>
                    <span className="qg-source-hint-icon"><Paperclip size={17}/></span>
                    <span><strong>Working from your own lectures?</strong> Add course materials for grounded answers.</span>
                    <ArrowRight size={16}/>
                  </button>
                </div>
              ) : messages.length === 0 && workspaceMode === "exam" ? (
                <div className="qg-exam-empty" aria-label="Exam assistant">
                  <div className="qg-exam-empty-symbol"><ClipboardList size={24}/></div>
                  <h1>Your exam, your questions.</h1>
                  <p>We'll shape the paper together, one decision at a time. Nothing is generated until you approve the details.</p>
                </div>
              ) : (
                <div className="qg-messages">
                  <div className="qg-conversation-meta"><span className="qg-meta-rule"/> <span>CONVERSATION NOTES</span> <span className="qg-meta-rule"/></div>
                  {messages.map((message) => (
                    <article key={message.id} data-message-id={message.id} className={`qg-message qg-message--${message.role}`}>
                      {message.role === "assistant" && <div className="qg-assistant-symbol" aria-hidden="true"><PencilLine size={18}/></div>}
                      <div className="qg-message-content">
                        <div className="qg-message-label">{message.role === "assistant" ? "QUESTGEN" : "YOU"}</div>
                        {message.role === "user" ? (
                          <>
                            <div className={`qg-user-note ${editingMessageId === message.id ? "qg-user-note--editing" : ""}`}>{message.content}</div>
                            {!!message.documents?.length && <div className="qg-message-files">{message.documents.map((doc) => <span key={doc.id}><Paperclip size={12}/>{doc.filename}</span>)}</div>}
                            {editingMessageId === message.id && <div className="qg-message-editing-indicator"><Pencil size={12}/> Editing in the message box below</div>}
                          </>
                        ) : (
                          <div className="qg-assistant-text questgen-prose">
                            {message.role === "assistant" && (assistantActivity[message.id] || message.id.startsWith("pending-answer-")) && (() => {
                              const stats = assistantActivity[message.id];
                              const isPending = message.id.startsWith("pending-answer-");
                              const visibleTokens = Math.ceil((stats?.visibleChars ?? message.content.length) / 4);
                              const elapsed = stats ? (Math.max(0, activityNow - stats.started) / 1000).toFixed(1) : "0.0";
                              const expanded = activityExpanded[message.id] !== false;
                              return <div className="qg-ai-activity">
                                <button type="button" className="qg-ai-activity-toggle" aria-expanded={expanded} onClick={() => setActivityExpanded(old => ({...old, [message.id]: !expanded}))}>
                                  <Activity size={13}/> {isPending ? "Generating response" : "Response activity"} <span>~{visibleTokens} output tokens</span> {isPending && <span>{elapsed}s</span>} {expanded ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}
                                </button>
                                {expanded && <div className="qg-ai-activity-detail">{isPending ? "Searching context and preparing an answer…" : "Response completed."} Token count is an estimate of visible output, not private model reasoning.</div>}
                              </div>;
                            })()}
                            {message.content ? <FormattedMessage content={message.content}/> : <span className="qg-thinking"><Loader2 size={15} className="qg-spin"/> QuestGen is preparing a response…</span>}
                            {!!message.sources?.length && (
                              <div className="qg-citations">
                                <div className="qg-citations-title"><BookOpenText size={15}/> REFERENCED MATERIAL</div>
                                <div className="qg-citation-list">
                                  {message.sources.slice(0, 8).map((source, index) => (
                                    <a key={source.chunk_id} className="qg-citation" href={sourceLink(source)} target="_blank" rel="noopener noreferrer" title={source.snippet.slice(0, 400)}>
                                      <span>{index + 1}</span> {source.filename}{source.page ? ` · ${sourceLocation(source)}` : ""}{source.section ? ` · ${source.section}` : ""}<ArrowUpRight size={13}/>
                                    </a>
                                  ))}
                                </div>
                                {message.sources.length > 8 && (
                                  <details className="qg-citations-extra">
                                    <summary>Show {message.sources.length - 8} more referenced passages</summary>
                                    <div className="qg-citation-list">
                                      {message.sources.slice(8).map((source, index) => (
                                        <a key={source.chunk_id} className="qg-citation" href={sourceLink(source)} target="_blank" rel="noopener noreferrer" title={source.snippet.slice(0, 400)}>
                                          <span>{index + 9}</span> {source.filename}{source.page ? ` · ${sourceLocation(source)}` : ""}{source.section ? ` · ${source.section}` : ""}<ArrowUpRight size={13}/>
                                        </a>
                                      ))}
                                    </div>
                                  </details>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                        {(!!message.content.trim() || (!busy && !startingExam && !editingMessageId && !message.id.startsWith("pending-"))) && (
                          <div className={`qg-message-tools ${message.role === "user" ? "qg-message-tools--user" : ""}`}>
                            {!!message.content.trim() && (
                              <button type="button" onClick={() => void copyChatMessage(message)}
                                className={copyFeedback?.id === message.id ? `qg-message-copy--${copyFeedback.status}` : ""}
                                title={copyFeedback?.id === message.id && copyFeedback.status === "error" ? "Unable to copy this message" : "Copy message text"}
                                aria-label={copyFeedback?.id === message.id && copyFeedback.status === "copied" ? "Message copied" : `Copy ${message.role === "user" ? "message" : "QuestGen response"}`}>
                                {copyFeedback?.id === message.id && copyFeedback.status === "copied" ? <Check size={14}/> : <Copy size={14}/>}
                                <span>{copyFeedback?.id === message.id ? (copyFeedback.status === "copied" ? "Copied" : "Copy failed") : "Copy"}</span>
                              </button>
                            )}
                            {workspaceMode === "chat" && (message.mode || "chat") === "chat" && !busy && !startingExam && !editingMessageId && !message.id.startsWith("pending-") && (message.role === "user" ? (
                              <button type="button" onClick={() => editChatMessage(message)} title="Edit this message" aria-label="Edit message">
                                <Pencil size={14}/> <span>Edit</span>
                              </button>
                            ) : (
                              <button type="button" onClick={() => void send("", { regenerate_message_id: message.id })}
                                title="Regenerate this response" aria-label="Regenerate response">
                                <RefreshCw size={14}/> <span>Regenerate</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              )}

              {workspaceMode === "exam" && examIntroConfirmed && assessment && !examPaper && examSession?.status !== "approved" && (
                <section className="qg-assessment" aria-label="Assessment draft">
                  <div className="qg-assessment-head">
                    <span className="qg-overline">ASSESSMENT WORKFLOW</span>
                    <span className={`qg-assessment-status qg-assessment-status--${assessment.status}`}>
                      {assessment.status === "completed" ? <CheckCircle2 size={14}/> : <Loader2 size={14} className={assessment.status === "running" ? "qg-spin" : ""}/>}
                      {assessment.status.toUpperCase()}
                    </span>
                  </div>
                  <h2>{assessment.blueprint?.title || "Preparing assessment blueprint"}</h2>
                  <p className="qg-assessment-prompt">{assessment.prompt}</p>
                  {assessment.blueprint && <div className="qg-assessment-meta">
                    <span>{assessment.blueprint.questions.length} questions</span>
                    <span>{assessment.blueprint.total_marks} marks</span>
                    <span>{assessment.questions.length} saved</span>
                  </div>}
                  <div className="qg-assessment-progress"><div style={{ width: `${activities.at(-1)?.progress ?? (assessment.status === "completed" ? 100 : 0)}%` }}/></div>
                  <div className="qg-assessment-log" aria-live="polite">
                    {activities.slice(-5).map((item) => <div key={item.id}><span className="qg-assessment-log-stage">{item.stage}</span>{item.message}</div>)}
                  </div>
                  {assessment.error && <div role="alert" className="qg-assessment-error">{assessment.error} <button type="button" onClick={() => void retryAssessment()} disabled={startingExam}><RotateCcw size={15}/> Retry saved draft</button></div>}
                  {assessment.questions.map((question) => (
                    <QuestionEditor key={`${question.id}-${question.version}-${question.locked}-${question.review_status}`}
                      question={question} showAnswers={showAnswers}
                      disabled={assessment.status !== "completed" || editingQuestionId !== null || startingExam}
                      onChange={questionAction} fetchHistory={questionHistory}/>
                  ))}
                  {assessment.questions.length > 0 && <button className="qg-assessment-toggle" type="button" onClick={() => setShowAnswers((old) => !old)}>{showAnswers ? "Hide" : "Show"} answer key</button>}
                  {assessment.status === "completed" && <div className="qg-export-panel">
                    <span className="qg-overline">EXPORT YOUR ASSESSMENT</span>
                    <p>{assessment.questions.filter(q => q.review_status === "approved").length}/{assessment.questions.length} questions approved. Student exports require all approvals.</p>
                    <div className="qg-question-actions">
                      <button type="button" onClick={() => void downloadExam("instructor", "pdf")}>Instructor PDF</button>
                      <button type="button" onClick={() => void downloadExam("instructor", "docx")}>Instructor DOCX</button>
                      <button type="button" disabled={!assessment.questions.length || !assessment.questions.every(q => q.review_status === "approved")} onClick={() => void downloadExam("student", "pdf")}>Student PDF</button>
                      <button type="button" disabled={!assessment.questions.length || !assessment.questions.every(q => q.review_status === "approved")} onClick={() => void downloadExam("student", "docx")}>Student DOCX</button>
                    </div>
                  </div>}
                  <p className="qg-assessment-disclaimer">Question edits are versioned; regeneration never changes locked questions. Review the final exam before use.</p>
                </section>
              )}
            </div>
          </section>
          {showScrollDown && <button type="button" className="qg-scroll-latest" onClick={() => scrollToLatest(true)} aria-label="Scroll to latest message" title="Jump to latest message"><ArrowDown size={18}/><span>Latest</span></button>}

          <div className="qg-compose-region">
            <div className="qg-compose-inner">
              {error && <div role="alert" className="qg-error"><AlertCircle size={17}/><span>{error}</span>{/error|cannot reach|request failed|backend/i.test(error) && <button type="button" className="qg-error-retry" onClick={() => void reconnectWorkspace()}><RefreshCw size={14}/> Retry loading</button>}<button type="button" aria-label="Dismiss error" onClick={() => setError("")}><X size={17}/></button></div>}
              {workspaceMode === "exam" && !examIntroConfirmed ? (
                <div className="qg-exam-enter" role="region" aria-label="Enter exam mode">
                  <div className="qg-exam-enter-icon"><img src="/icon.svg" alt="" width={34} height={34}/></div>
                  <div className="qg-exam-enter-copy"><span className="qg-exam-enter-kicker">QUESTGEN · EXAM STUDIO</span><strong>{examSession?.status === "approved" ? "Pick up where you left off" : "Let's shape your next exam"}</strong><span>We'll work through the details together. You choose what makes the paper.</span></div>
                  <button type="button" className="qg-exam-enter-confirm" disabled={examBusy || busy || !examSession} onClick={() => morphComposer(() => {
                    if (threadId) window.localStorage.setItem(examIntroKey(threadId), "true");
                    setExamIntroConfirmed(true);
                    setError("");
                  })}>
                    {examBusy ? <Loader2 size={15} className="qg-spin"/> : <Check size={16}/>} {examSession?.status === "approved" ? "Open my paper" : "Start with QuestGen"} <ArrowRight size={15}/>
                  </button>
                </div>
              ) : <>
              {workspaceMode === "exam" && examSession && <ExamConfiguration
                session={examSession} documents={libraryDocuments} selectedDocuments={attachedDocuments}
                busy={examBusy || busy} onChoose={submitExamQuickReply}
                onSave={saveExamRequirements} onApprove={approveExam}
                onSelectMaterials={() => openLibrary("picker")}
                controlsTarget={hitlActionsTarget}>
                {examIntroConfirmed && examSession.status === "approved" && <ExamPaperBuilder
                  paper={examPaper} requirements={examSession.requirements} busy={paperBusy || examBusy}
                  onPrepare={() => paperAction("/prepare")}
                  onChangeBlueprint={(blueprint: PaperBlueprint) => paperAction("/blueprint", "PUT", { blueprint })}
                  onApproveBlueprint={() => paperAction("/approve")}
                  onNext={() => paperAction("/next")}
                  onAccept={() => paperAction("/accept", "POST", { candidate_id: examPaper?.candidate_id })}
                  onRegenerate={() => paperAction("/regenerate", "POST", { candidate_id: examPaper?.candidate_id })}
                  onSkip={() => paperAction("/skip", "POST", { candidate_id: examPaper?.candidate_id })}
                  onEditCandidate={(changes: QuestionEdits) => paperAction("/candidate", "PATCH", { candidate_id: examPaper?.candidate_id, ...changes })}
                  onCancelGeneration={cancelPaperGeneration}
                />}
              </ExamConfiguration>}
              {workspaceMode === "exam" && <div ref={setHitlActionsTarget} className="qg-hitl-composer-actions" aria-label="Exam choices and question controls"/>}
              <form className={`qg-composer ${editingMessageId ? "qg-composer--editing" : ""}`} onSubmit={onSubmit}>
                <div className="qg-composer-topline"><span>{editingMessageId ? "EDIT MESSAGE" : "YOUR NEXT THOUGHT"}</span><span className="qg-compose-line"/></div>
                {editingMessageId && (
                  <div className="qg-composer-edit-banner" role="status">
                    <span><Pencil size={14}/> Editing your earlier message. Later replies will be replaced.</span>
                    <button type="button" onClick={cancelMessageEdit} disabled={busy} aria-label="Cancel message editing"><X size={14}/> Cancel</button>
                  </div>
                )}
                {!editingMessageId && <div className="qg-compose-scope" role="status">
                  {attachedDocuments.length > 0
                    ? `Using ${attachedDocuments.length} selected ${attachedDocuments.length === 1 ? "material" : "materials"} for this conversation`
                    : libraryDocuments.some((doc) => doc.status === "ready")
                      ? `Using all ${libraryDocuments.filter((doc) => doc.status === "ready").length} ready course materials by default`
                      : libraryDocuments.some((doc) => doc.status !== "failed")
                        ? "Course materials are still indexing; wait until they are ready"
                        : "No indexed course materials yet; upload files to ground your questions"}
                </div>}
                {attachedDocuments.length > 0 && <div className="qg-attached-list" aria-label="Attached course materials">
                  {attachedDocuments.map((doc) => <span key={doc.id} className="qg-attached-chip"><Files size={13}/><span title={doc.filename}>{doc.filename}</span><button type="button" aria-label={`Remove ${doc.filename}`} onClick={() => setAttachedDocuments((old) => old.filter((item) => item.id !== doc.id))}><X size={13}/></button></span>)}
                </div>}
                <textarea ref={textRef} rows={1} maxLength={12000} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={onKeyDown} disabled={busy || examBusy || !courseId || (workspaceMode === "chat" && !settings.model)} aria-label={editingMessageId ? "Edit message in composer" : "Message QuestGen"} placeholder={workspaceMode === "exam" ? "Answer the current question, or tell me your exam requirements…" : !settings.model ? "Choose a model in the top bar to begin…" : "Ask a question, learn, or discuss any topic…"}/>
                <div className="qg-composer-actions">
                  {editingMessageId ? (
                    <span className="qg-edit-help">Enter to save · Shift+Enter for new line · Esc to cancel</span>
                  ) : (
                    <>
                      <button type="button" onClick={() => openLibrary("picker")} disabled={!courseId} className="qg-attach"><Paperclip size={17}/><span>Attach course material</span></button>
                      <span className="qg-keyboard-tip">↵ to send · shift ↵ for new line</span>
                      {workspaceMode === "exam" && assessment?.status === "completed" && !examPaper && <button type="button" className="qg-assessment-action" onClick={() => void customizeAssessment()}
                        disabled={!input.trim() || busy || startingExam || editingQuestionId !== null} title="Apply conversational edits to question(s) in this assessment">
                        <PencilLine size={15}/> Apply to draft
                      </button>}
                      {workspaceMode === "exam" && <span className="qg-exam-gate-hint"><ShieldCheck size={14}/> {examSession?.status === "approved" ? "Configuration approved · review the blueprint to generate questions" : "Professor approval required"}</span>}
                    </>
                  )}
                  {busy ? (
                    <button type="button" onClick={() => abortRef.current?.abort()} className="qg-send" title="Stop response" aria-label="Stop response"><Square size={15} fill="currentColor"/></button>
                  ) : (
                    <button type="submit" disabled={!input.trim() || !courseId || examBusy || (workspaceMode === "chat" && !settings.model)} className="qg-send" aria-label={editingMessageId ? "Save edit and regenerate response" : "Send message"} title={editingMessageId ? "Save edit & regenerate" : "Send message"}>{editingMessageId ? <Check size={20}/> : <ArrowUp size={21}/>}</button>
                  )}
                </div>
              </form>
              </>}
              <div className="qg-compose-footer"><span>{workspaceMode === "exam" ? "EXAM · INTERACTIVE PAPER BUILDER" : "QUESTGEN · GENERAL CHAT"}</span><span>Review AI-generated content before use</span></div>
            </div>
          </div>
          </div>
          {workspaceMode === "exam" && examIntroConfirmed && examSession?.status === "approved" && <ExamPaperSidebar
            paper={examPaper} requirements={examSession.requirements} busy={paperBusy}
            onEdit={(id, changes) => paperAction(`/questions/${id}`, "PATCH", changes)}
            onRemove={id => paperAction(`/questions/${id}`, "DELETE")}
            onReorder={question_ids => paperAction("/order", "PUT", {question_ids})}
            onFinalApprove={() => paperAction("/final-approve")}
            onDownload={downloadPaper}
          />}
        </div>
        {documentsOpen && courseId && <DocumentPanel key={`${courseId}:${documentMode}`} courseId={courseId} courseName={currentCourse?.name || "Course"}
          mode={documentMode} selectedIds={attachedDocuments.map((doc) => doc.id)}
          onSelect={setAttachedDocuments} onChanged={() => void refreshLibrary(courseId).catch(() => {})}
          onClose={() => { setDocumentsOpen(false); void refreshLibrary(courseId).catch(() => {}); }}/>}
      </main>

      {courseToDelete && (
        <div className="qg-modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !deletingCourse) { setCourseToDelete(null); setDeleteConfirmName(""); }
        }}>
          <div className="qg-dialog qg-delete-course-dialog" role="alertdialog" aria-modal="true"
            aria-labelledby="qg-delete-course-title" aria-describedby="qg-delete-course-description">
            <div className="qg-dialog-head">
              <div><span className="qg-overline">PERMANENT DELETION</span><h2 id="qg-delete-course-title">Delete course?</h2></div>
              <button type="button" className="qg-icon-button" aria-label="Close confirmation" disabled={deletingCourse}
                onClick={() => { setCourseToDelete(null); setDeleteConfirmName(""); }}><X size={19}/></button>
            </div>
            <p id="qg-delete-course-description" className="qg-delete-course-warning">
              Deleting <strong>{courseToDelete.name}</strong> permanently removes its uploaded files, indexed materials,
              conversations, citations, generated assessments, and related records. Other courses are not affected.
              This cannot be undone.
            </p>
            <label className="qg-delete-course-label" htmlFor="qg-delete-course-confirm">
              Type <strong>{courseToDelete.name}</strong> to confirm
            </label>
            <input id="qg-delete-course-confirm" className="qg-delete-course-input" autoFocus autoComplete="off"
              value={deleteConfirmName} onChange={(event) => setDeleteConfirmName(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Escape" && !deletingCourse) { setCourseToDelete(null); setDeleteConfirmName(""); } }} />
            {courseDeleteError && <p role="alert" className="qg-delete-course-error">{courseDeleteError}</p>}
            <div className="qg-delete-course-actions">
              <button type="button" className="qg-delete-cancel" disabled={deletingCourse}
                onClick={() => { setCourseToDelete(null); setDeleteConfirmName(""); }}>Cancel</button>
              <button type="button" className="qg-delete-confirm" disabled={busy || deletingCourse || deleteConfirmName !== courseToDelete.name}
                onClick={() => void confirmDeleteCourse()}>
                {deletingCourse ? <Loader2 size={16} className="qg-spin"/> : <Trash2 size={16}/>}
                {deletingCourse ? "Deleting…" : "Delete course and data"}
              </button>
            </div>
          </div>
        </div>
      )}
      {(settingsOpen || courseOpen) && (
        <div className="qg-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) { setSettingsOpen(false); setCourseOpen(false); } }}>
          <div role="dialog" aria-modal="true" aria-label={settingsOpen ? "Model settings" : "New course"} className="qg-dialog">
            <div className="qg-dialog-head"><div><span className="qg-overline">{settingsOpen ? "MODEL PREFERENCES" : "YOUR COURSE LIBRARY"}</span><h2>{settingsOpen ? "Choose a provider" : "New course"}</h2></div><button type="button" className="qg-icon-button" aria-label="Close dialog" onClick={() => { setSettingsOpen(false); setCourseOpen(false); }}><X size={19}/></button></div>
            {settingsOpen ? (
              <div className="qg-settings-sections">
                <div className="qg-settings-appearance" role="group" aria-label="Appearance">
                  <div className="qg-settings-appearance-copy"><strong>Appearance</strong><span>Warm paper or muted midnight sage.</span></div>
                  <div className="qg-theme-options">
                    <button type="button" aria-pressed={theme === "light"} onClick={() => chooseTheme("light")}><Sun size={15}/> Light</button>
                    <button type="button" aria-pressed={theme === "dark"} onClick={() => chooseTheme("dark")}><Moon size={15}/> Dark</button>
                  </div>
                </div>
              <form onSubmit={saveSettings} className="qg-dialog-form qg-connection-form">
                <label>AI provider</label>
                <PencilSelect ariaLabel="AI provider" placement="dialog" value={settingsDraft.provider}
                  options={PROVIDERS.map((provider) => ({ value: provider.value, label: provider.label, detail: provider.detail }))}
                  onChange={(provider) => changeProviderDraft(provider as ModelSettings["provider"])}/>
                <div className="qg-connection-status">
                  <ShieldCheck size={16} aria-hidden="true"/>
                  <span>{connectionsLoading ? "Checking configuration…" :
                    selectedConnection?.key_source === "saved" ? "API key saved on this device" :
                    selectedConnection?.key_source === "environment" ? "API key provided by server environment" :
                    selectedConnection?.requires_key ? "API key not configured" :
                    "API key optional for local connection"}</span>
                </div>
                <div className="qg-field-heading">
                  <label htmlFor="provider-api-key">API key {selectedConnection?.requires_key ? <span className="qg-required">required</span> : <span className="qg-optional">optional</span>}</label>
                  {PROVIDER_KEY_LINKS[settingsDraft.provider] && <a href={PROVIDER_KEY_LINKS[settingsDraft.provider]} target="_blank" rel="noopener noreferrer">Get API key <ArrowUpRight size={12}/></a>}
                </div>
                <div className="qg-secret-input">
                  <input id="provider-api-key" type={showProviderKey ? "text" : "password"}
                    autoComplete="new-password" spellCheck={false} maxLength={2048}
                    value={providerKey} disabled={connectionsLoading || clearProviderKey}
                    onChange={(event) => setProviderKey(event.target.value)}
                    placeholder={selectedConnection?.key_configured ? "Saved · enter to replace" : "Paste API key here"}/>
                  <button type="button" className="qg-visibility-toggle" aria-label={showProviderKey ? "Hide API key" : "Show API key"}
                    onClick={() => setShowProviderKey((visible) => !visible)}>{showProviderKey ? <EyeOff size={16}/> : <Eye size={16}/>}</button>
                </div>
                <p className="qg-connection-help">{settingsDraft.provider === "ollama" ? "Local Ollama normally needs no key; use this only for an authenticated proxy." :
                  settingsDraft.provider === "9router" ? "Copy the API key from the 9Router dashboard. Leave blank only if your gateway allows it." :
                  "Stored on the local Python backend. Leave blank to keep your current key."}</p>
                {selectedConnection?.key_source === "saved" && <label className="qg-clear-key"><input type="checkbox" checked={clearProviderKey}
                  onChange={(event) => { setClearProviderKey(event.target.checked); if (event.target.checked) setProviderKey(""); }}/>
                  Remove saved API key</label>}
                {localProvider && <>
                  <label htmlFor="provider-base-url">API base URL</label>
                  <input id="provider-base-url" type="url" required spellCheck={false} autoCapitalize="none" maxLength={500}
                    value={providerBase} onChange={(event) => setProviderBase(event.target.value)}
                    placeholder={CONNECTION_DEFAULTS[settingsDraft.provider as "9router" | "ollama"]}/>
                  <p className="qg-connection-help">{settingsDraft.provider === "9router" ? "9Router default: http://127.0.0.1:20128/v1" : "Ollama default: http://127.0.0.1:11434"}. Remote connections require HTTPS.</p>
                </>}
                <div className="qg-dialog-note"><PenLine size={17}/><p>Provider credentials go to the local backend only. After connecting, choose an available model in the chat header. No model ID to enter here.</p></div>
                {connectionProblem && <p className="qg-connection-feedback qg-connection-feedback--bad" role="alert">{connectionProblem}</p>}
                {connectionFeedback && <p className="qg-connection-feedback qg-connection-feedback--good" role="status">{connectionFeedback}</p>}
                <div className="qg-connection-actions">
                  <button type="button" className="qg-test-provider" onClick={() => void testProvider()}
                    disabled={saving || testingConnection || connectionsLoading}>{testingConnection ? "Testing…" : "Save & test connection"}</button>
                  <button type="submit" className="qg-primary-submit" disabled={saving || testingConnection || connectionsLoading || busy}>
                    {saving ? "Saving…" : "Save provider"} <ArrowRight size={17}/>
                  </button>
                </div>
              </form>
              </div>
            ) : (
              <form onSubmit={addCourse} className="qg-dialog-form">
                <label htmlFor="course-name">Course name</label>
                <input id="course-name" autoFocus required maxLength={80} autoComplete="off" value={courseName} onChange={(event) => setCourseName(event.target.value)} placeholder="e.g. Data Structures"/>
                <button type="submit" className="qg-primary-submit" disabled={saving || !courseName.trim()}>{saving ? "Creating…" : "Create course"} <ArrowRight size={17}/></button>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
