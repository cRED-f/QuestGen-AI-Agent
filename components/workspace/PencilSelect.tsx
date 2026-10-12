"use client";

import { Check, ChevronDown, Loader2, RefreshCcw, Search, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export type PickerOption = { value: string; label: string; detail?: string };

// Some providers return the model ID as its display name. Show it once.
function distinctDetail(option: PickerOption): string {
  const detail = option.detail?.trim() || "";
  return detail && detail.toLocaleLowerCase() !== option.label.trim().toLocaleLowerCase() ? detail : "";
}

type Props = {
  ariaLabel: string;
  value: string;
  options: PickerOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  placement?: "sidebar" | "topbar" | "dialog";
  loading?: boolean;
  error?: string;
  onRefresh?: () => void;
  disabled?: boolean;
  searchable?: boolean;
  onDeleteOption?: (value: string) => void;
  deletingOption?: string | null;
};

export default function PencilSelect({ ariaLabel, value, options, onChange,
  placeholder = "Choose an option", placement = "dialog", loading, error,
  onRefresh, disabled, searchable = false, onDeleteOption, deletingOption }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [open]);
  useEffect(() => { if (open && searchable) searchRef.current?.focus(); }, [open, searchable]);
  const selected = options.find((item) => item.value === value);
  const selectedDetail = selected ? distinctDetail(selected) : "";
  const shown = options.filter((item) => `${item.label} ${item.value}`.toLowerCase().includes(query.toLowerCase()));
  return <div className={`qg-pencil-picker qg-pencil-picker--${placement}`} ref={root}>
    <button type="button" className={`qg-pencil-trigger ${open ? "qg-pencil-trigger--open" : ""}`} aria-label={ariaLabel}
      aria-haspopup="listbox" aria-expanded={open} disabled={disabled}
      onClick={() => { setOpen((previous) => !previous); setQuery(""); }}>
      <span className="qg-pencil-trigger-copy"><span className="qg-pencil-trigger-main">{selected?.label || value || placeholder}</span>
      {placement === "topbar" && (selectedDetail || (!value ? "Click to choose a model" : "")) &&
        <span className="qg-pencil-trigger-sub">{selectedDetail || "Click to choose a model"}</span>}</span>
      <ChevronDown size={15} aria-hidden="true"/>
    </button>
    {open && <div className="qg-pencil-menu" role="group" aria-label={ariaLabel}>
      <div className="qg-pencil-menu-heading"><span>{ariaLabel}</span>
        {onRefresh && <button type="button" onClick={onRefresh} aria-label="Refresh available models" title="Refresh available models" className="qg-pencil-refresh" disabled={loading}><RefreshCcw size={14}/></button>}
      </div>
      {searchable && <div className="qg-pencil-search"><Search size={15} aria-hidden="true"/>
        <input ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search available models…" aria-label="Search models"/>
      </div>}
      <div className="qg-pencil-options" role="listbox" aria-label={ariaLabel}>
        {loading ? <div className="qg-pencil-empty"><Loader2 size={15} className="qg-spin"/> Loading models…</div> : error ?
          <div className="qg-pencil-empty qg-pencil-error">{error}</div> : shown.length === 0 ?
            <div className="qg-pencil-empty">{options.length ? "No matching models." : "No models available. Check the provider connection."}</div> :
          shown.map((item) => <div key={item.value} role="presentation" className="qg-pencil-option-row">
            <button role="option" aria-selected={value === item.value} type="button" className="qg-pencil-option"
              onClick={() => { onChange(item.value); setOpen(false); setQuery(""); }}>
              <span className="qg-pencil-option-content"><strong>{item.label}</strong>{distinctDetail(item) && <small>{distinctDetail(item)}</small>}</span>
              {value === item.value && <Check size={16} aria-hidden="true"/>}
            </button>
            {onDeleteOption && <button type="button" className="qg-course-remove" disabled={disabled || deletingOption === item.value}
              aria-label={`Delete course ${item.label}`} title={`Delete ${item.label} and all its data`}
              onClick={() => { setOpen(false); onDeleteOption(item.value); }}>
              {deletingOption === item.value ? <Loader2 size={15} className="qg-spin"/> : <Trash2 size={15}/>}
            </button>}
          </div>)}
      </div>
      {placement === "topbar" && <div className="qg-pencil-menu-foot">Only models returned by your selected provider are listed.</div>}
    </div>}
  </div>;
}
