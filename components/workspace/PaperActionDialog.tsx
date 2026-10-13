"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ArrowRight, Check, Loader2, X } from "lucide-react";

/** A single, theme-aware confirmation for consequential paper actions. */
export default function PaperActionDialog({
  eyebrow, title, description, icon, details = [], note, confirmLabel,
  busy = false, caution = false, error, onCancel, onConfirm,
}: {
  eyebrow: string;
  title: string;
  description: string;
  icon: ReactNode;
  details?: { label: string; value: string }[];
  note?: string;
  confirmLabel: string;
  busy?: boolean;
  caution?: boolean;
  error?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();

  // The exam sidebar animates with transform. Portal to the app shell to avoid
  // a fixed overlay getting constrained to that sidebar. Keep theme variables.
  useEffect(() => {
    setHost(document.querySelector<HTMLElement>(".qg-app"));
  }, []);

  useEffect(() => {
    if (!host) return;
    previousFocus.current = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => previousFocus.current?.focus();
  }, [host]);

  useEffect(() => {
    if (!host) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!busy) onCancel();
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialogRef.current?.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current?.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [host, busy, onCancel]);

  if (!host) return null;
  return createPortal(
    <div className="qg-paper-action-overlay" onMouseDown={event => {
      if (event.target === event.currentTarget && !busy) onCancel();
    }}>
      <div ref={dialogRef} className="qg-paper-action-dialog" role={caution ? "alertdialog" : "dialog"} aria-modal="true"
        aria-labelledby={titleId} aria-describedby={descriptionId}>
        <div className="qg-paper-action-heading">
          <span className="qg-paper-action-icon" aria-hidden="true">{icon}</span>
          <button className="qg-paper-action-close" type="button" aria-label="Close confirmation" disabled={busy} onClick={onCancel}>
            <X size={17}/>
          </button>
        </div>
        <span className="qg-paper-action-kicker">{eyebrow}</span>
        <h2 id={titleId}>{title}</h2>
        <p className="qg-paper-action-description" id={descriptionId}>{description}</p>
        {details.length > 0 && <dl className="qg-paper-action-details">
          {details.map(detail => <div key={detail.label}><dt>{detail.label}</dt><dd>{detail.value}</dd></div>)}
        </dl>}
        {note && <p className={`qg-paper-action-note ${caution ? "qg-paper-action-note--caution" : ""}`}>
          {note}
        </p>}
        {error && <p className="qg-paper-action-error" role="alert">{error}</p>}
        <div className="qg-paper-action-footer">
          <button type="button" className="qg-paper-action-cancel" ref={cancelRef} disabled={busy} onClick={onCancel}>Cancel</button>
          <button type="button" className="qg-paper-action-confirm" disabled={busy} onClick={onConfirm}>
            {busy ? <Loader2 size={15} className="qg-spin"/> : caution ? <ArrowRight size={15}/> : <Check size={15}/>}
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>, host,
  );
}
