"use client";

import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowRight, ArrowUpRight, FileText, Loader2, Search, Trash2, UploadCloud, X } from "lucide-react";

const API = "/api/workspace";

export type Source = {
  chunk_id: string;
  document_id: string;
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
  bytes: number;
  status: "queued" | "extracting" | "chunking" | "indexing" | "ready" | "failed";
  progress: number;
  error: string | null;
  warning: string | null;
  pages: number;
  chunk_count: number;
  deduplicated?: boolean;
};

export function sourceLink(source: Source): string {
  // The browser's native PDF viewer navigates directly to the cited page.
  return `${source.source_url}${source.page ? `#page=${source.page}` : ""}`;
}

export function DocumentPanel({ courseId, onClose }: { courseId: string; onClose: () => void }) {
  const [items, setItems] = useState<DocumentRecord[]>([]);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<Source[]>([]);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [error, setError] = useState("");
  const [removingId, setRemovingId] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
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

  const upload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || uploadProgress !== null) return;
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
    };
    xhr.onerror = () => { setUploadProgress(null); setError("Could not reach the document service."); };
    xhr.send(data);
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
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete document.");
    } finally { setRemovingId(null); }
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

  return (
    <>
      <button type="button" className="qg-document-scrim" onClick={onClose} aria-label="Close course materials" />
      <aside className="qg-document-panel" aria-label="Course documents">
        <div className="qg-document-header">
          <div><span className="qg-overline">YOUR REFERENCE SHELF</span><h2>Course materials<span>.</span></h2><p>Everything you need, right where you work.</p></div>
          <button type="button" className="qg-icon-button" onClick={onClose} aria-label="Close documents"><X size={20}/></button>
        </div>
        <div className="qg-document-content">
          <div className="qg-upload-zone">
            <input ref={fileInput} type="file" className="qg-hidden-input" accept=".pdf,.docx,.txt,.md" onChange={upload} aria-label="Choose course document" />
            <button type="button" className="qg-upload-button" onClick={() => fileInput.current?.click()} disabled={uploadProgress !== null || !courseId}>
              <span className="qg-upload-icon">{uploadProgress !== null ? <Loader2 size={22} className="qg-spin"/> : <UploadCloud size={23} strokeWidth={1.5}/>}</span>
              <strong>{uploadProgress !== null ? `Uploading · ${uploadProgress}%` : "Add some course material"}</strong>
              <span>PDF, DOCX, TXT or Markdown · up to 15 MiB</span>
            </button>
            {uploadProgress !== null && <div role="progressbar" aria-label="Uploading course material" aria-valuenow={uploadProgress} aria-valuemin={0} aria-valuemax={100} className="qg-progress"><div style={{ width: `${uploadProgress}%` }}/></div>}
          </div>
          {error && <div role="alert" className="qg-error qg-document-error"><AlertCircle size={17}/><span>{error}</span><button type="button" onClick={() => setError("")} aria-label="Dismiss document message"><X size={16}/></button></div>}
          <section className="qg-document-section" aria-label="Uploaded files">
            <div className="qg-document-section-title"><span className="qg-overline">01 / YOUR FILES</span><span className="qg-count">{items.length}</span></div>
            {items.length === 0 && <div className="qg-doc-empty"><FileText size={19}/><p>No documents yet. Add lecture notes or slides to make them searchable.</p></div>}
            <div className="qg-file-list">
              {items.map((doc) => (
                <div className="qg-file-item" key={doc.id}>
                  <div className="qg-file-icon"><FileText size={20} strokeWidth={1.65}/></div>
                  <div className="qg-file-details">
                    <strong title={doc.filename}>{doc.filename}</strong>
                    <span className={`qg-file-status ${doc.status === "failed" ? "qg-file-status--bad" : doc.status === "ready" ? "qg-file-status--ready" : ""}`}>
                      {doc.status === "ready" ? `${doc.chunk_count} passages${doc.pages ? ` · ${doc.pages} pages` : ""}` : doc.status === "failed" ? "Extraction failed" : `${doc.status} · ${doc.progress}%`}
                    </span>
                    {!["ready", "failed"].includes(doc.status) && <div className="qg-progress"><div style={{ width: `${doc.progress}%` }}/></div>}
                    {doc.error && <span className="qg-file-warning" role="status">{doc.error}</span>}
                    {doc.warning && <span className="qg-file-warning">{doc.warning}</span>}
                  </div>
                  <div className="qg-file-actions">
                    <a className="qg-mini-icon" href={`${API}/courses/${encodeURIComponent(courseId)}/documents/${encodeURIComponent(doc.id)}/file`} target="_blank" rel="noopener noreferrer" title={`Open ${doc.filename}`} aria-label={`Open ${doc.filename}`}><ArrowUpRight size={17}/></a>
                    <button type="button" className="qg-mini-icon qg-danger-hover" onClick={() => void remove(doc.id)} disabled={removingId !== null} aria-label={`Delete ${doc.filename}`} title="Delete file"><Trash2 size={16}/></button>
                  </div>
                </div>
              ))}
            </div>
          </section>
          <section className="qg-document-section" aria-label="Search course materials">
            <div className="qg-document-section-title"><span className="qg-overline">02 / FIND A PASSAGE</span></div>
            <form className="qg-document-search" onSubmit={(event) => { event.preventDefault(); void runSearch(); }}>
              <Search size={17}/><input value={search} maxLength={500} onChange={(event) => setSearch(event.target.value)} placeholder="Try ‘binary search complexity’" aria-label="Search course content"/>
              <button type="submit" disabled={!search.trim() || loadingSearch} aria-label="Search documents">{loadingSearch ? <Loader2 size={16} className="qg-spin"/> : <ArrowRight size={18}/>}</button>
            </form>
            {results.length === 0 && search && !loadingSearch && <p className="qg-search-empty">No matching excerpts yet. Only fully indexed documents are searchable.</p>}
            <div className="qg-search-results">{results.map((source, index) => (
              <div className="qg-search-result" key={source.chunk_id}>
                <div className="qg-result-heading"><span className="qg-result-number">{String(index + 1).padStart(2,"0")}</span><a href={sourceLink(source)} target="_blank" rel="noopener noreferrer">{source.filename}{source.page ? ` · p. ${source.page}` : ""}{source.section ? ` · ${source.section}` : ""} <ArrowUpRight size={13}/></a></div>
                <p>{source.snippet}</p>
              </div>
            ))}</div>
          </section>
        </div>
        <div className="qg-document-footer"><span className="qg-status-dot"/> Files are stored on your local backend</div>
      </aside>
    </>
  );
}
