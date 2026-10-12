"use client";

import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";

type Choice = { value: string; label: string };

type ChoiceSelectProps = {
  label: string;
  value: string;
  options: Choice[];
  onChange: (value: string) => void;
  disabled?: boolean;
};

type MenuPosition = { top: number; left: number; width: number; maxHeight: number };

/** Custom themed listbox: the menu is portaled so the HITL panel cannot clip it. */
export default function ExamChoiceSelect({ label, value, options, onChange, disabled = false }: ChoiceSelectProps) {
  const id = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const [position, setPosition] = useState<MenuPosition | null>(null);
  const selectedIndex = options.findIndex(option => option.value === value);
  const selected = options[selectedIndex];

  function openMenu() {
    if (disabled || options.length === 0 || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const gap = 6;
    const availableBelow = window.innerHeight - rect.bottom - gap - 8;
    const availableAbove = rect.top - gap - 8;
    const desired = Math.min(224, options.length * 41 + 12);
    const upward = availableBelow < Math.min(145, desired) && availableAbove > availableBelow;
    const maxHeight = Math.max(56, Math.min(desired, upward ? availableAbove : availableBelow));
    const width = Math.min(Math.max(rect.width, 172), window.innerWidth - 16);
    setPosition({
      top: upward ? Math.max(8, rect.top - gap - maxHeight) : rect.bottom + gap,
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      width,
      maxHeight,
    });
    setHighlighted(Math.max(0, selectedIndex));
    setOpen(true);
  }

  function closeMenu(restoreFocus = false) {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }

  function selectChoice(index: number) {
    const choice = options[index];
    if (!choice) return;
    if (choice.value !== value) onChange(choice.value);
    closeMenu(true);
  }

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node;
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) closeMenu();
    }
    function onScroll(event: Event) {
      if (!menuRef.current?.contains(event.target as Node)) closeMenu();
    }
    function onResize() { closeMenu(); }
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open]);

  // If the parent replaces this field, its popup must not remain open.
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (!open && ["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      openMenu();
      return;
    }
    if (!open) return;
    if (event.key === "Escape" || event.key === "Tab") { closeMenu(); return; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setHighlighted(index => event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectChoice(highlighted);
    }
  }

  const menuStyle: CSSProperties | undefined = position ? {
    top: position.top,
    left: position.left,
    width: position.width,
    maxHeight: position.maxHeight,
  } : undefined;

  return <>
    <button
      type="button"
      ref={triggerRef}
      className="qg-exam-choice-trigger"
      role="combobox"
      aria-label={label}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={open ? `${id}-choices` : undefined}
      aria-activedescendant={open ? `${id}-option-${highlighted}` : undefined}
      disabled={disabled}
      onClick={() => open ? closeMenu() : openMenu()}
      onKeyDown={onKeyDown}
    >
      <span>{selected?.label ?? "Choose an option"}</span><ChevronDown size={15} aria-hidden="true" className={open ? "qg-exam-choice-chevron--open" : ""}/>
    </button>
    {open && position && createPortal(
      <div id={`${id}-choices`} ref={menuRef} className="qg-exam-choice-popover" role="listbox" aria-label={label} style={menuStyle}>
        {options.map((option, index) => <button
          key={option.value}
          type="button"
          id={`${id}-option-${index}`}
          className="qg-exam-choice-option"
          role="option"
          aria-selected={option.value === value}
          data-highlighted={index === highlighted ? "true" : undefined}
          tabIndex={-1}
          onMouseEnter={() => setHighlighted(index)}
          onClick={() => selectChoice(index)}
        ><span>{option.label}</span>{option.value === value && <Check size={14} aria-hidden="true"/>}</button>)}
      </div>, document.body,
    )}
  </>;
}
