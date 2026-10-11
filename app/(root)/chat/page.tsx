"use client";

import {
  AlertCircle,
  ArrowUp,
  ArrowRight,
  ArrowUpRight,
  BookOpenText,
  ChevronRight,
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
  RotateCcw,
  CheckCircle2,
  Menu,
  MessageSquareText,
  Plus,
  Settings2,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { DocumentPanel, Source, sourceLink } from "@/components/workspace/DocumentPanel";
import QuestionEditor, { type AssessmentQuestion } from "@/components/workspace/QuestionEditor";
import PencilSelect from "@/components/workspace/PencilSelect";

type Course = { id: string; name: string; created_at: number };
type Thread = { id: string; course_id: string; title: string; updated_at: number };
type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: number;
  sources?: Source[];
};
type AssessmentActivity = { id: number; stage: string; message: string; progress: number };
type AssessmentJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  prompt: string;
  blueprint: null | { title: string; instructions: string; total_marks: number; questions: unknown[] };
  questions: AssessmentQuestion[];
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
const SUGGESTIONS = [
  "Design a 40-mark midterm assessment for a data structures course",
  "Suggest a Bloom's taxonomy distribution for a university exam",
  "Help me improve the clarity of my multiple-choice questions",
];

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(result.detail || `Request failed (${response.status})`);
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
  const [courseOpen, setCourseOpen] = useState(false);
  const [courseName, setCourseName] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [input, setInput] = useState("");
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLElement | null>(null);
  const textRef = useRef<HTMLTextAreaElement | null>(null);
  const savedComposerDraftRef = useRef("");
  const currentCourse = courses.find((course) => course.id === courseId);
  const currentThread = threads.find((thread) => thread.id === threadId);

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
    window.localStorage.setItem("questgen:course", courseId);
    setThreads([]);
    setDocumentsOpen(false);
    setThreadId(null);
    setMessages([]);
    setEditingMessageId(null);
    setInput((current) => editingMessageId ? savedComposerDraftRef.current : current);
    savedComposerDraftRef.current = "";
    setAssessment(null);
    setActivities([]);
    refreshThreads(courseId).then((available) => {
      if (!active) return;
      const last = window.localStorage.getItem(`questgen:thread:${courseId}`);
      if (last && available.some((item) => item.id === last)) {
        setThreadId(last);
        requestJson<ChatMessage[]>(`/threads/${last}/messages`)
          .then((items) => active && setMessages(items))
          .catch((cause: Error) => active && setError(cause.message));
      }
    }).catch((cause: Error) => active && setError(cause.message));
    return () => { active = false; };
  }, [courseId, refreshThreads]);

  const refreshAssessment = useCallback(async (id: string) => {
    const jobs = await requestJson<AssessmentJob[]>(`/threads/${id}/generations`);
    setAssessment(jobs[0] || null);
    setActivities(jobs[0]?.events || []);
  }, []);

  useEffect(() => {
    if (!threadId) { setAssessment(null); setActivities([]); return; }
    let active = true;
    requestJson<AssessmentJob[]>(`/threads/${threadId}/generations`).then((jobs) => {
      if (!active) return;
      setAssessment(jobs[0] || null);
      setActivities(jobs[0]?.events || []);
    }).catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [threadId]);

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

  useEffect(() => {
    // Only scroll the conversation viewport, never the document or outer workspace.
    if (messages.length) {
      const viewport = scrollRef.current;
      viewport?.scrollTo({ top: viewport.scrollHeight, behavior: busy ? "instant" : "smooth" });
    }
  }, [messages, busy]);

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
    setThreadId(null);
    setMessages([]);
    setEditingMessageId(null);
    setInput((current) => editingMessageId ? savedComposerDraftRef.current : current);
    savedComposerDraftRef.current = "";
    setAssessment(null);
    setActivities([]);
    setError("");
    setSidebarOpen(false);
    if (courseId) window.localStorage.removeItem(`questgen:thread:${courseId}`);
    textRef.current?.focus();
  };

  const openThread = async (id: string) => {
    if (busy || id === threadId) return;
    setError("");
    setThreadId(id);
    setEditingMessageId(null);
    if (editingMessageId) setInput(savedComposerDraftRef.current);
    savedComposerDraftRef.current = "";
    setAssessment(null);
    setActivities([]);
    setSidebarOpen(false);
    window.localStorage.setItem(`questgen:thread:${courseId}`, id);
    try {
      setMessages(await requestJson<ChatMessage[]>(`/threads/${id}/messages`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load conversation.");
    }
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
    if ((!content && !action?.regenerate_message_id) || !courseId || busy || startingExam ||
        (editingMessageId !== null && action?.edit_message_id !== editingMessageId)) return;
    if (action && !threadId) return;
    const existingId = threadId;
    setBusy(true);
    setError("");
    if (!action) setInput("");
    let editWasAccepted = false;
    const controller = new AbortController();
    abortRef.current = controller;
    let activeId = existingId;
    const timestamp = Date.now();
    const user: ChatMessage = {
      id: `pending-user-${timestamp}`, role: "user", content, created_at: timestamp / 1000,
    };
    const answer: ChatMessage = {
      id: `pending-answer-${timestamp}`, role: "assistant", content: "", created_at: timestamp / 1000,
    };
    setMessages((previous) => {
      if (action?.edit_message_id) {
        const index = previous.findIndex((item) => item.id === action.edit_message_id);
        if (index < 0) return previous;
        return [...previous.slice(0, index), { ...previous[index], content }, answer];
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
      const response = await fetch(`${API}/threads/${activeId}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(action?.regenerate_message_id ? {} : { content }),
          ...action,
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
          setMessages((previous) => previous.map((item) => item.id === answer.id
            ? { ...item, content: item.content + (event.content || "") } : item));
        }
        if (event.type === "done" && event.message_id) {
          setMessages((previous) => previous.map((item) => item.id === answer.id ? { ...item, id: event.message_id! } : item));
        }
      });
    } catch (cause) {
      setError(controller.signal.aborted ? "Response stopped. Incomplete responses are not saved." :
        (cause instanceof Error ? cause.message : "Could not generate a response."));
    } finally {
      abortRef.current = null;
      setBusy(false);
      if (editWasAccepted) {
        setEditingMessageId(null);
        setInput(savedComposerDraftRef.current);
        savedComposerDraftRef.current = "";
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
    if (!editingMessageId) savedComposerDraftRef.current = input;
    setEditingMessageId(message.id);
    setInput(message.content);
    setError("");
  };

  const cancelMessageEdit = () => {
    if (!editingMessageId || busy) return;
    setEditingMessageId(null);
    setInput(savedComposerDraftRef.current);
    savedComposerDraftRef.current = "";
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
        method: "POST", body: JSON.stringify({ prompt }),
      });
      setAssessment(job);
      setActivities(job.events || []);
      setInput("");
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
    <div className="qg-app">
      {sidebarOpen && (
        <button type="button" aria-label="Close navigation" className="qg-mobile-scrim" onClick={() => setSidebarOpen(false)} />
      )}
      <aside className={`qg-sidebar ${sidebarOpen ? "qg-sidebar--open" : ""}`} aria-label="Workspace navigation">
        <div className="qg-brand">
          <div className="qg-brand-mark" aria-hidden="true"><PencilLine size={25} strokeWidth={1.8} /></div>
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
              onChange={setCourseId} placeholder="Choose a course"/>
          </div>

          <div className="qg-side-section qg-library-section">
            <div className="qg-section-header"><span className="qg-overline">02 / LIBRARY</span></div>
            <button type="button" className="qg-library-link" onClick={() => { setDocumentsOpen(true); setSidebarOpen(false); }} disabled={!courseId}>
              <Files size={18} strokeWidth={1.8}/><span>Course materials</span><ArrowUpRight size={15} className="qg-link-tail"/>
            </button>
            <div className="qg-library-note">Upload lectures, find passages, and cite your sources.</div>
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
            <span className="qg-settings-copy"><strong>Model settings</strong><small>{PROVIDERS.find((item) => item.value === settings.provider)?.label} · {settings.model || "Choose model in chat"}</small></span>
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
          <div className="qg-topbar-right">
            <span className="qg-current-label" title={currentThread?.title || "New conversation"}>{currentThread?.title || "Untitled conversation"}</span>
            <button type="button" className="qg-topbar-button" onClick={() => setDocumentsOpen(true)} disabled={!courseId}><Files size={17}/><span>Materials</span></button>
            <PencilSelect ariaLabel={`${PROVIDERS.find((item) => item.value === settings.provider)?.label || "Provider"} models`} placement="topbar" value={settings.model}
              placeholder="Choose a model" disabled={busy || startingExam}
              options={availableModels.map((item) => ({ value: item.id, label: item.name, detail: item.id }))}
              searchable loading={modelsLoading} error={modelsError}
              onRefresh={() => setModelRefresh((previous) => previous + 1)} onChange={(id) => void selectModel(id)}/>
          </div>
        </header>

        <div className="qg-paper-workspace">
          <section ref={scrollRef} className={`qg-chat-scroll ${!loading && messages.length === 0 && !assessment ? "qg-chat-scroll--welcome" : ""}`} aria-live="polite" aria-label="Conversation">
            <div className="qg-chat-frame">
              {loading ? (
                <div className="qg-loading"><Loader2 size={20} className="qg-spin"/><span>Opening your workspace…</span></div>
              ) : messages.length === 0 ? (
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
                  <button type="button" onClick={() => setDocumentsOpen(true)} className="qg-source-hint" disabled={!courseId}>
                    <span className="qg-source-hint-icon"><Paperclip size={17}/></span>
                    <span><strong>Working from your own lectures?</strong> Add course materials for grounded answers.</span>
                    <ArrowRight size={16}/>
                  </button>
                </div>
              ) : (
                <div className="qg-messages">
                  <div className="qg-conversation-meta"><span className="qg-meta-rule"/> <span>CONVERSATION NOTES</span> <span className="qg-meta-rule"/></div>
                  {messages.map((message) => (
                    <article key={message.id} className={`qg-message qg-message--${message.role}`}>
                      {message.role === "assistant" && <div className="qg-assistant-symbol" aria-hidden="true"><PencilLine size={18}/></div>}
                      <div className="qg-message-content">
                        <div className="qg-message-label">{message.role === "assistant" ? "QUESTGEN" : "YOU"}</div>
                        {message.role === "user" ? (
                          <>
                            <div className={`qg-user-note ${editingMessageId === message.id ? "qg-user-note--editing" : ""}`}>{message.content}</div>
                            {editingMessageId === message.id && <div className="qg-message-editing-indicator"><Pencil size={12}/> Editing in the message box below</div>}
                          </>
                        ) : (
                          <div className="qg-assistant-text questgen-prose">
                            {message.content ? <ReactMarkdown>{message.content}</ReactMarkdown> : <span className="qg-thinking"><Loader2 size={15} className="qg-spin"/> Thinking through your question…</span>}
                            {!!message.sources?.length && (
                              <div className="qg-citations">
                                <div className="qg-citations-title"><BookOpenText size={15}/> REFERENCED MATERIAL</div>
                                <div className="qg-citation-list">
                                  {message.sources.map((source, index) => (
                                    <a key={source.chunk_id} className="qg-citation" href={sourceLink(source)} target="_blank" rel="noopener noreferrer" title={source.snippet.slice(0, 400)}>
                                      <span>{index + 1}</span> {source.filename}{source.page ? ` · p. ${source.page}` : ""}{source.section ? ` · ${source.section}` : ""}<ArrowUpRight size={13}/>
                                    </a>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                        {!busy && !startingExam && !editingMessageId && !message.id.startsWith("pending-") && (
                          <div className={`qg-message-tools ${message.role === "user" ? "qg-message-tools--user" : ""}`}>
                            {message.role === "user" ? (
                              <button type="button" onClick={() => editChatMessage(message)} title="Edit this message" aria-label="Edit message">
                                <Pencil size={14}/> <span>Edit</span>
                              </button>
                            ) : (
                              <button type="button" onClick={() => void send("", { regenerate_message_id: message.id })}
                                title="Regenerate this response" aria-label="Regenerate response">
                                <RefreshCw size={14}/> <span>Regenerate</span>
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              )}
              {assessment && (
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

          <div className="qg-compose-region">
            <div className="qg-compose-inner">
              {error && <div role="alert" className="qg-error"><AlertCircle size={17}/><span>{error}</span><button type="button" aria-label="Dismiss error" onClick={() => setError("")}><X size={17}/></button></div>}
              <form className={`qg-composer ${editingMessageId ? "qg-composer--editing" : ""}`} onSubmit={onSubmit}>
                <div className="qg-composer-topline"><span>{editingMessageId ? "EDIT MESSAGE" : "YOUR NEXT THOUGHT"}</span><span className="qg-compose-line"/></div>
                {editingMessageId && (
                  <div className="qg-composer-edit-banner" role="status">
                    <span><Pencil size={14}/> Editing your earlier message. Later replies will be replaced.</span>
                    <button type="button" onClick={cancelMessageEdit} disabled={busy} aria-label="Cancel message editing"><X size={14}/> Cancel</button>
                  </div>
                )}
                <textarea ref={textRef} rows={2} maxLength={12000} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={onKeyDown} disabled={busy || !courseId || !settings.model} aria-label={editingMessageId ? "Edit message in composer" : "Message QuestGen"} placeholder={!settings.model ? "Choose a model in the top bar to begin…" : assessment?.status === "completed" ? "e.g. Make question 2 harder, or lock question 1…" : "Ask a question, refine an idea, or plan an assessment…"}/>
                <div className="qg-composer-actions">
                  {editingMessageId ? (
                    <span className="qg-edit-help">Enter to save · Shift+Enter for new line · Esc to cancel</span>
                  ) : (
                    <>
                      <button type="button" onClick={() => setDocumentsOpen(true)} disabled={!courseId} className="qg-attach"><Paperclip size={17}/><span>Attach course material</span></button>
                      <span className="qg-keyboard-tip">↵ to send · shift ↵ for new line</span>
                      {assessment?.status === "completed" && <button type="button" className="qg-assessment-action" onClick={() => void customizeAssessment()}
                        disabled={!input.trim() || busy || startingExam || editingQuestionId !== null} title="Apply conversational edits to question(s) in this assessment">
                        <PencilLine size={15}/> Apply to draft
                      </button>}
                      <button type="button" className="qg-assessment-action" onClick={() => void startAssessment()} disabled={!input.trim() || !courseId || !settings.model || busy || startingExam || assessment?.status === "running" || assessment?.status === "queued"} title="Generate a structured assessment from these instructions">
                        {startingExam ? <Loader2 size={15} className="qg-spin"/> : <ClipboardList size={16}/>} Generate exam
                      </button>
                    </>
                  )}
                  {busy ? (
                    <button type="button" onClick={() => abortRef.current?.abort()} className="qg-send" title="Stop response" aria-label="Stop response"><Square size={15} fill="currentColor"/></button>
                  ) : (
                    <button type="submit" disabled={!input.trim() || !courseId || !settings.model} className="qg-send" aria-label={editingMessageId ? "Save edit and regenerate response" : "Send message"} title={editingMessageId ? "Save edit & regenerate" : "Send message"}>{editingMessageId ? <Check size={20}/> : <ArrowUp size={21}/>}</button>
                  )}
                </div>
              </form>
              <div className="qg-compose-footer"><span>QUESTGEN · COURSE-BASED ASSISTANCE</span><span>Review AI-generated content before use</span></div>
            </div>
          </div>
        </div>
        {documentsOpen && courseId && <DocumentPanel courseId={courseId} onClose={() => setDocumentsOpen(false)}/>}
      </main>

      {(settingsOpen || courseOpen) && (
        <div className="qg-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) { setSettingsOpen(false); setCourseOpen(false); } }}>
          <div role="dialog" aria-modal="true" aria-label={settingsOpen ? "Model settings" : "New course"} className="qg-dialog">
            <div className="qg-dialog-head"><div><span className="qg-overline">{settingsOpen ? "MODEL PREFERENCES" : "YOUR COURSE LIBRARY"}</span><h2>{settingsOpen ? "Choose a provider" : "New course"}</h2></div><button type="button" className="qg-icon-button" aria-label="Close dialog" onClick={() => { setSettingsOpen(false); setCourseOpen(false); }}><X size={19}/></button></div>
            {settingsOpen ? (
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
