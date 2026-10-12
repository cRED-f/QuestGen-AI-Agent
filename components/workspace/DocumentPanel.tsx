"use client";

import { ChangeEvent, DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowRight, ArrowUpRight, Check, FileText, FolderOpen, Loader2, RefreshCcw, Search, ShieldCheck, Trash2, UploadCloud, X } from "lucide-react";

const API = "/api/workspace";

export type CourseMaterial = {
  id: string;
  filename: string;
  extension?: string;
  status?: "queued" | "extracting" | "chunking" | "indexing" | "ready" | "failed";
};

export type Source = {
  chunk_id: string;
  document_id: string;
  extension: string;
  filename: string;
  page: number | null;
  section: string | null;
  snippet: string;
  source_url: string;
  score?: number;
};

type DocumentRecord = {
  id: string;
  filename: string;
  extension: string;
  bytes: number;
  status: "queued" | "extracting" | "chunking" | "indexing" | "ready" | "failed";
  progress: number;
  error: string | null;
  warning: string | null;
  pages: number;
  chunk_count: number;
  processed_units: number;
  indexed_units: number;
  vision_units: number;
  deduplicated?: boolean;
};

export function sourceLink(source: Source): string {
  // Only PDFs support the browser's #page= fragment. A PPTX page means slide number.
  return `${source.source_url}${source.page && source.extension === ".pdf" ? `#page=${source.page}` : ""}`;
}

export function sourceLocation(source: Source): string {
  return source.page ? `${source.extension === ".pptx" ? "slide" : "p."} ${source.page}` : "";
}

export function DocumentPanel({ courseId, courseName, mode, selectedIds = [], onSelect, onChanged, onClose }: {
  courseId: string;
  courseName: string;
  mode: "library" | "picker";
  selectedIds?: string[];
  onSelect?: (materials: CourseMaterial[]) => void;
  onChanged?: () => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<DocumentRecord[]>([]);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<string[]>(selectedIds);
  const [results, setResults] = useState<Source[]>([]);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [error, setError] = useState("");
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [reprocessingId, setReprocessingId] = useState<string | null>(null);
  const [ingestionStatus, setIngestionStatus] = useState<{ available: boolean; renderer_available: boolean; renderer_message?: string; message: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const requestSeq = useRef(0);

  const refresh = useCallback(async () => {
    if (!courseId) return;
    const response = await fetch(`${API}/courses/${encodeURIComponent(courseId)}/documents`, { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load documents.");
    setItems((await response.json()) as DocumentRecord[]);
  }, [courseId]);

  useEffect(() => {
    setItems([]);
    setResults([]);
    setError("");
    void refresh().catch((cause: Error) => setError(cause.message));
    const timer = window.setInterval(() => void refresh().catch(() => {}), 1600);
    return () => window.clearInterval(timer);
  }, [refresh]);

  // Keep keyboard focus inside the dialog; restore it to the opener on close.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return; }
      if (event.key !== "Tab") return;
      const dialog = closeButton.current?.closest('[role="dialog"]');
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href], [tabindex="0"]'))
        .filter((node) => node.getClientRects().length > 0);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); opener?.focus({ preventScroll: true }); };
  }, []);

  const uploadFile = (file?: File) => {
    if (!file) return;
    if (uploadProgress !== null) return;
    if (!/\.(pdf|docx|pptx|txt|md|png|jpg|jpeg|webp|bmp|tif|tiff)$/i.test(file.name)) {
      setError("Unsupported format. Choose a PDF, document, slide deck, text file or image.");
      return;
    }
    if (file.size > 15 * 1024 * 1024) {
      setError("Maximum upload size is 15 MiB.");
      return;
    }
    setError("");
    setUploadProgress(0);
    const data = new FormData();
    data.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}/courses/${encodeURIComponent(courseId)}/documents`);
    xhr.upload.onprogress = (progress) => {
      if (progress.lengthComputable) setUploadProgress(Math.round((progress.loaded / progress.total) * 100));
    };
    xhr.onload = () => {
      setUploadProgress(null);
      if (xhr.status < 200 || xhr.status >= 300) {
        let message = `Upload failed (${xhr.status}).`;
        try { message = JSON.parse(xhr.responseText).detail || message; } catch { /* Keep fallback */ }
        setError(message);
      } else {
        try {
          const result = JSON.parse(xhr.responseText) as DocumentRecord;
          if (result.deduplicated) setError("This course already contains that file. Reusing the existing document.");
        } catch { /* Server accepted upload. */ }
      }
      void refresh().catch((cause: Error) => setError(cause.message));
      if (xhr.status >= 200 && xhr.status < 300) onChanged?.();
    };
    xhr.onerror = () => { setUploadProgress(null); setError("Could not reach the document service."); };
    xhr.send(data);
  };

  const upload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    uploadFile(file);
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) uploadFile(event.dataTransfer.files[0]);
  };

  const remove = async (id: string) => {
    if (removingId) return;
    setRemovingId(id);
    setError("");
    try {
      const response = await fetch(`${API}/courses/${encodeURIComponent(courseId)}/documents/${id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("Could not delete document.");
      setItems((old) => old.filter((item) => item.id !== id));
      setResults((old) => old.filter((item) => item.document_id !== id));
      await refresh();
      setPicked((old) => old.filter((value) => value !== id));
      onChanged?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete document.");
    } finally { setRemovingId(null); }
  };

  const reprocess = async (id: string) => {
    if (reprocessingId) return;
    setReprocessingId(id);
    setError("");
    try {
      const response = await fetch(`${API}/courses/${encodeURIComponent(courseId)}/documents/${encodeURIComponent(id)}/reprocess`, { method: "POST" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Could not reprocess document.");
      }
      await refresh();
      onChanged?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not reprocess document.");
    } finally { setReprocessingId(null); }
  };

  const checkIngestion = async () => {
    try {
      const response = await fetch(`${API}/ingestion/status`, { cache: "no-store" });
      if (!response.ok) throw new Error("Ingestion diagnostics unavailable.");
      const status = await response.json() as { available: boolean; renderer_available: boolean; renderer_message?: string; message: string };
      setIngestionStatus(status);
    } catch { setIngestionStatus({ available: false, renderer_available: false, message: "Could not check document processing." }); }
  };

  const runSearch = async () => {
    if (!search.trim()) { setResults([]); return; }
    const token = ++requestSeq.current;
    setLoadingSearch(true);
    setError("");
    try {
      const response = await fetch(`${API}/courses/${encodeURIComponent(courseId)}/search?query=${encodeURIComponent(search.trim())}&limit=8`);
      if (!response.ok) throw new Error("Document search failed.");
      const entries = (await response.json()) as Source[];
      if (token === requestSeq.current) setResults(entries);
    } catch (cause) {
      if (token === requestSeq.current) setError(cause instanceof Error ? cause.message : "Search failed.");
    } finally { if (token === requestSeq.current) setLoadingSearch(false); }
  };

  const visible = items.filter((doc) => doc.filename.toLowerCase().includes(filter.toLowerCase().trim()));
  const selectable = items.filter((doc) => doc.status === "ready");
  const checked = picked.filter((id) => selectable.some((doc) => doc.id === id));
  const toggle = (id: string) => setPicked((old) => old.includes(id)
    ? old.filter((value) => value !== id) : old.length < 30 ? [...old, id] : old);

  const readyCount = selectable.length;
  const indexingCount = items.filter((doc) => !["ready", "failed"].includes(doc.status)).length;

  return (
    <div className="qg-material-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="qg-document-panel" role="dialog" aria-modal="true" aria-labelledby="qg-material-title" aria-describedby="qg-material-description">
        <div className="qg-document-header">
          <div className="qg-material-heading">
            <span className="qg-material-kicker"><FolderOpen size={15}/> COURSE WORKSPACE / {mode === "picker" ? "ATTACH" : "LIBRARY"}</span>
            <h2 id="qg-material-title">{mode === "picker" ? "Choose course materials" : "Course materials"}</h2>
            <p id="qg-material-description">{courseName} · Files stay within this course and power grounded assessments.</p>
          </div>
          <button ref={closeButton} type="button" className="qg-icon-button qg-material-close" onClick={onClose} aria-label="Close course materials"><X size={19}/></button>
        </div>
        <div className="qg-material-stats" aria-label="Course materials summary">
          <span><strong>{items.length}</strong> files uploaded</span>
          <span><Check size={14}/><strong>{readyCount}</strong> ready to use</span>
          {indexingCount > 0 && <span><Loader2 size={13} className="qg-spin"/><strong>{indexingCount}</strong> processing</span>}
          <span className="qg-material-private"><ShieldCheck size={15}/> Course-scoped</span>
        </div>
        <div className="qg-document-content">
          <div className={`qg-upload-zone ${dragging ? "qg-upload-zone--dragging" : ""}`}
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
            onDrop={onDrop}>
            <input ref={fileInput} type="file" className="qg-hidden-input" accept=".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff" onChange={upload} aria-label="Choose course document" />
            <div className="qg-upload-icon">{uploadProgress !== null ? <Loader2 size={21} className="qg-spin"/> : <UploadCloud size={22}/>}</div>
            <div className="qg-upload-copy"><strong>{uploadProgress !== null ? `Uploading · ${uploadProgress}%` : dragging ? "Drop your file here" : "Add lecture notes, slides or documents"}</strong>
              <span>Drag a file here, or browse your computer. PDF, DOCX, PPTX, text and images · max 15 MiB</span></div>
            <button type="button" className="qg-upload-browse" onClick={() => fileInput.current?.click()} disabled={uploadProgress !== null || !courseId}>Browse files <ArrowUpRight size={15}/></button>
            {uploadProgress !== null && <div role="progressbar" aria-label="Uploading course material" aria-valuenow={uploadProgress} aria-valuemin={0} aria-valuemax={100} className="qg-progress qg-upload-progress"><div style={{ width: `${uploadProgress}%` }}/></div>}
          </div>
          {error && <div role="alert" className="qg-error qg-document-error"><AlertCircle size={17}/><span>{error}</span><button type="button" onClick={() => setError("")} aria-label="Dismiss document message"><X size={16}/></button></div>}
          <section className="qg-document-section" aria-label="Uploaded files">
            <label className="qg-library-filter"><Search size={16}/><input aria-label="Filter library files" placeholder="Find a file in this course…" value={filter} onChange={(event) => setFilter(event.target.value)}/></label>
            {mode === "picker" && <div className="qg-picker-intro"><Check size={15}/> Select specific files to limit sources. Leave everything unselected to use all ready materials in this course.</div>}
            <div className="qg-document-section-title"><span className="qg-overline">{mode === "picker" ? "AVAILABLE SOURCES" : "YOUR FILES"}</span>
              {mode === "picker" ? <div className="qg-picker-bulk"><button type="button" onClick={() => setPicked(selectable.slice(0, 30).map((doc) => doc.id))} disabled={!selectable.length}>Select ready</button><button type="button" onClick={() => setPicked([])} disabled={!picked.length}>Clear</button></div> : <span className="qg-count">{visible.length}</span>}
            </div>
            {items.length === 0 && <div className="qg-doc-empty"><FileText size={20}/><p>Your course library is empty. Upload a presentation or document to get started.</p></div>}
            {items.length > 0 && !visible.length && <div className="qg-doc-empty"><Search size={20}/><p>No matching files. Try a different name.</p></div>}
            <div className="qg-file-list">
              {visible.map((doc) => (
                <div className={`qg-file-item ${mode === "picker" && picked.includes(doc.id) ? "qg-file-item--selected" : ""}`} key={doc.id}>
                  {mode === "picker" && <label className="qg-picker-check"><input type="checkbox" aria-label={`Select ${doc.filename}`} disabled={doc.status !== "ready"} checked={picked.includes(doc.id) && doc.status === "ready"} onChange={() => toggle(doc.id)}/></label>}
                  <div className="qg-file-icon"><FileText size={20} strokeWidth={1.65}/></div>
                  <div className="qg-file-details">
                    <strong title={doc.filename}>{doc.filename}</strong>
                    <span className={`qg-file-status ${doc.status === "failed" ? "qg-file-status--bad" : doc.status === "ready" ? "qg-file-status--ready" : ""}`}>
                      {doc.status === "ready" ? `${doc.chunk_count} passages${doc.pages ? ` · ${doc.pages} ${doc.filename.toLowerCase().endsWith(".pptx") ? "slides" : "pages"}` : ""}` : doc.status === "failed" ? "Extraction failed" : `${doc.status} · ${doc.progress}%`}
                    </span>
                    {doc.status === "ready" && (doc.extension === ".pptx" || doc.filename.toLowerCase().endsWith(".pptx") || doc.filename.toLowerCase().endsWith(".pdf")) && (
                      <span className="qg-file-status">Visual analysis: {doc.vision_units || 0}/{doc.processed_units || doc.pages || 0} {doc.filename.toLowerCase().endsWith(".pptx") ? "slides" : "units"} · {doc.indexed_units || 0} with content</span>
                    )}
                    {!["ready", "failed"].includes(doc.status) && <div className="qg-progress"><div style={{ width: `${doc.progress}%` }}/></div>}
                    {doc.error && <span className="qg-file-warning" role="status">{doc.error}</span>}
                    {doc.warning && <span className="qg-file-warning">{doc.warning}</span>}
                  </div>
                  <div className="qg-file-actions">
                    {(doc.status === "ready" || doc.status === "failed") && (
                      <button type="button" className="qg-mini-icon" onClick={() => void reprocess(doc.id)}
                        disabled={reprocessingId !== null}
                        title={doc.status === "ready" && doc.processed_units > doc.vision_units && (doc.extension === ".pptx" || doc.extension === ".pdf")
                          ? "Retry missing visual analyses (successful slide results are cached)" : "Reprocess using current extraction model"}
                        aria-label={doc.status === "ready" && doc.processed_units > doc.vision_units && (doc.extension === ".pptx" || doc.extension === ".pdf")
                          ? `Retry missing visual analysis for ${doc.filename}` : `Reprocess ${doc.filename}`}>
                        <RefreshCcw size={16} className={reprocessingId === doc.id ? "qg-spin" : ""}/>
                      </button>
                    )}
                    <a className="qg-mini-icon" href={`${API}/courses/${encodeURIComponent(courseId)}/documents/${encodeURIComponent(doc.id)}/file`} target="_blank" rel="noopener noreferrer" title={`Open ${doc.filename}`} aria-label={`Open ${doc.filename}`}><ArrowUpRight size={17}/></a>
                    {mode === "library" && <button type="button" className="qg-mini-icon qg-danger-hover" onClick={() => void remove(doc.id)} disabled={removingId !== null} aria-label={`Delete ${doc.filename}`} title="Delete file"><Trash2 size={16}/></button>}
                  </div>
                </div>
              ))}
            </div>
          </section>
          <section className="qg-document-section qg-document-passage" aria-label="Search course materials">
            <div className="qg-document-section-title"><span className="qg-overline">SEARCH INSIDE MATERIALS</span></div>
            <form className="qg-document-search" onSubmit={(event) => { event.preventDefault(); void runSearch(); }}>
              <Search size={17}/><input value={search} maxLength={500} onChange={(event) => setSearch(event.target.value)} placeholder="Try ‘binary search complexity’" aria-label="Search course content"/>
              <button type="submit" disabled={!search.trim() || loadingSearch} aria-label="Search documents">{loadingSearch ? <Loader2 size={16} className="qg-spin"/> : <ArrowRight size={18}/>}</button>
            </form>
            {results.length === 0 && search && !loadingSearch && <p className="qg-search-empty">No matching excerpts yet. Only fully indexed documents are searchable.</p>}
            <div className="qg-search-results">{results.map((source, index) => (
              <div className="qg-search-result" key={source.chunk_id}>
                <div className="qg-result-heading"><span className="qg-result-number">{String(index + 1).padStart(2,"0")}</span><a href={sourceLink(source)} target="_blank" rel="noopener noreferrer">{source.filename}{source.page ? ` · ${sourceLocation(source)}` : ""}{source.section ? ` · ${source.section}` : ""} <ArrowUpRight size={13}/></a></div>
                <p>{source.snippet}</p>
              </div>
            ))}</div>
          </section>
        </div>
        {mode === "picker" ? <div className="qg-picker-footer">
          <div className="qg-picker-selection"><strong>{checked.length ? `${checked.length} selected` : "All ready materials"}</strong><span>{checked.length ? "Only these files will be used" : "Default source scope · up to 30 selected"}</span></div>
          <button type="button" className="qg-picker-apply" onClick={() => { onSelect?.(selectable.filter((doc) => checked.includes(doc.id))); onClose(); }}>
            <FileText size={15}/> Use {checked.length ? `${checked.length} ${checked.length === 1 ? "material" : "materials"}` : "all course materials"} <ArrowRight size={16}/>
          </button>
        </div> : <div className="qg-document-footer"><span><ShieldCheck size={15}/> Materials are isolated by course</span><button type="button" onClick={() => void checkIngestion()}>Check vision support</button>{ingestionStatus && <span role="status" className={ingestionStatus.available && ingestionStatus.renderer_available ? "qg-ocr-ok" : "qg-file-warning"}>{ingestionStatus.message}</span>}</div>}
      </section>
    </div>
  );
}
