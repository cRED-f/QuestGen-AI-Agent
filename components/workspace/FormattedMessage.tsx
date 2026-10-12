"use client";

import { Check, Copy } from "lucide-react";
import { Children, isValidElement, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { writeMessageToClipboard } from "@/lib/clipboard";

type TextPart = { kind: "markdown"; value: string };
type TablePart = { kind: "table"; headings: string[]; align: Array<"left" | "right" | "center" | undefined>; rows: string[][] };
type Part = TextPart | TablePart;

/** Split cells while preserving escaped pipes and pipes inside inline code spans. */
function cells(line: string): string[] {
  let source = line.trim();
  if (source.startsWith("|")) source = source.slice(1);
  if (source.endsWith("|") && !source.endsWith("\\|")) source = source.slice(0, -1);
  const result: string[] = [];
  let current = "";
  let escaped = false;
  let ticks = 0;
  for (let i = 0; i < source.length; i += 1) {
    const char = source[i];
    if (escaped) { current += char; escaped = false; continue; }
    if (char === "\\") { current += char; escaped = true; continue; }
    if (char === "`") {
      let count = 1;
      while (source[i + count] === "`") count += 1;
      if (ticks === count) ticks = 0;
      else if (!ticks) ticks = count;
      current += "`".repeat(count);
      i += count - 1;
      continue;
    }
    if (char === "|" && !ticks) { result.push(current.trim()); current = ""; continue; }
    current += char;
  }
  result.push(current.trim());
  return result;
}

function delimiterAlignment(line: string, expected: number): TablePart["align"] | null {
  const parts = cells(line);
  if (parts.length !== expected || parts.some((part) => !/^:?-{3,}:?$/.test(part))) return null;
  return parts.map((part) => part.startsWith(":") && part.endsWith(":") ? "center" : part.endsWith(":") ? "right" : part.startsWith(":") ? "left" : undefined);
}

/**
 * Some model providers collapse a complete Markdown table onto one line:
 * "Question ... | Col A | Col B | |---|---| | x | y |".
 * Recreate line boundaries only when a valid header, delimiter and at least one
 * complete row are present. Ordinary prose containing pipes stays unchanged.
 */
export function expandFlattenedTable(line: string): string[] {
  const separator = /\|\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|/g;
  for (const match of Array.from(line.matchAll(separator))) {
    const delimiterStart = match.index ?? -1;
    if (delimiterStart < 0) continue;
    const align = cells(match[0]);
    const columnCount = align.length;
    if (columnCount < 2 || columnCount > 12 || !delimiterAlignment(match[0], columnCount)) continue;
    const before = line.slice(0, delimiterStart);
    // A proper one-line header must have n+1 border pipes before the separator.
    const bars = Array.from(before.matchAll(/(?<!\\)\|/g), (bar) => bar.index ?? -1);
    if (bars.length < columnCount + 1) continue;
    const headerStart = bars[bars.length - columnCount - 1];
    const header = before.slice(headerStart).trim();
    const headings = cells(header);
    if (headings.length !== columnCount || headings.some((heading) => !heading)) continue;

    let rest = line.slice(delimiterStart + match[0].length);
    const rows: string[] = [];
    while (rows.length < 100) {
      const candidate = rest.trimStart();
      if (!candidate.startsWith("|")) break;
      let cursor = 1;
      let inCode = 0;
      let escaped = false;
      let complete = true;
      for (let col = 0; col < columnCount; col += 1) {
        let next = -1;
        for (let pos = cursor; pos < candidate.length; pos += 1) {
          const char = candidate[pos];
          if (escaped) { escaped = false; continue; }
          if (char === "\\") { escaped = true; continue; }
          if (char === "`") {
            let length = 1;
            while (candidate[pos + length] === "`") length += 1;
            if (inCode === length) inCode = 0;
            else if (!inCode) inCode = length;
            pos += length - 1;
            continue;
          }
          if (char === "|" && !inCode) { next = pos; break; }
        }
        if (next < 0) { complete = false; break; }
        cursor = next + 1;
      }
      if (!complete) break;
      rows.push(candidate.slice(0, cursor).trimEnd());
      rest = candidate.slice(cursor);
    }
    if (!rows.length) continue;
    const introduction = before.slice(0, headerStart).trim();
    const trailing = rest.trim();
    return [
      ...(introduction ? [introduction, ""] : []),
      header, match[0], ...rows,
      ...(trailing ? ["", trailing] : []),
    ];
  }
  return [line];
}

/**
 * The current dependency lock has CommonMark but no GFM plugin. Convert valid
 * pipe tables into table elements without reinterpreting fenced code blocks.
 * This avoids a new un-lockable package and leaves other Markdown untouched.
 */
export function splitMessageMarkdown(markdown: string): Part[] {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const parts: Part[] = [];
  let buffer: string[] = [];
  let fenceChar = "";
  let fenceLength = 0;
  const flush = () => { if (buffer.length) parts.push({ kind: "markdown", value: buffer.join("\n") }); buffer = []; };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const fence = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) {
      const marker = fence[1];
      if (!fenceChar) { fenceChar = marker[0]; fenceLength = marker.length; }
      else if (marker[0] === fenceChar && marker.length >= fenceLength && !line.slice(fence[0].length).trim()) { fenceChar = ""; fenceLength = 0; }
      buffer.push(line);
      continue;
    }
    if (!fenceChar && line.includes("|")) {
      const expanded = expandFlattenedTable(line);
      if (expanded.length > 1) {
        lines.splice(i, 1, ...expanded);
        i -= 1;
        continue;
      }
    }
    if (!fenceChar && i + 1 < lines.length && line.includes("|")) {
      const headings = cells(line);
      const align = headings.length >= 2 ? delimiterAlignment(lines[i + 1], headings.length) : null;
      if (align) {
        flush();
        const rows: string[][] = [];
        i += 1;
        while (i + 1 < lines.length && lines[i + 1].includes("|") && lines[i + 1].trim()) {
          const row = cells(lines[i + 1]);
          if (row.length !== headings.length) break;
          rows.push(row);
          i += 1;
        }
        parts.push({ kind: "table", headings, align, rows });
        continue;
      }
    }
    buffer.push(line);
  }
  flush();
  return parts;
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const code = Children.toArray(children).find((child) => isValidElement(child));
  const props = isValidElement<{ className?: string; children?: ReactNode }>(code) ? code.props : null;
  const language = /language-([\w+.-]+)/.exec(props?.className || "")?.[1] || "code";
  const text = String(props?.children ?? "").replace(/\n$/, "");
  const [status, setStatus] = useState<"idle" | "copied" | "error">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const copy = async () => {
    try { await writeMessageToClipboard(text); setStatus("copied"); }
    catch { setStatus("error"); }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setStatus("idle"), 2200);
  };
  return <div className="qg-code-block">
    <div className="qg-code-toolbar"><span>{language}</span><button type="button" onClick={() => void copy()} aria-label={`Copy ${language} code`}>
      {status === "copied" ? <Check size={14}/> : <Copy size={14}/>} {status === "copied" ? "Copied" : status === "error" ? "Copy failed" : "Copy code"}
    </button></div>
    <pre>{children}</pre>
  </div>;
}

/** Small GFM additions for the dependency-locked CommonMark renderer. */
function inlineStrike(children: ReactNode): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child !== "string" || !child.includes("~~")) return child;
    return child.split(/(~~[^~\n]+~~)/g).map((piece, index) =>
      piece.startsWith("~~") && piece.endsWith("~~") ? <del key={index}>{piece.slice(2, -2)}</del> : piece);
  });
}

function ListItem({ children }: { children?: ReactNode }) {
  const items = Children.toArray(children);
  const first = items[0];
  const match = typeof first === "string" ? /^\[([ xX])\]\s*/.exec(first) : null;
  if (!match) return <li>{inlineStrike(children)}</li>;
  return <li className="qg-task-item">
    <input type="checkbox" checked={match[1].toLowerCase() === "x"} readOnly aria-label={match[1].toLowerCase() === "x" ? "Completed item" : "Incomplete item"}/>
    <span>{inlineStrike([first.slice(match[0].length), ...items.slice(1)])}</span>
  </li>;
}

/** Inline cells still support links, emphasis and code spans. */
function CellContent({ content }: { content: string }) {
  return <ReactMarkdown components={{ p: ({ children }) => <>{inlineStrike(children)}</> }}>{content}</ReactMarkdown>;
}

export default function FormattedMessage({ content }: { content: string }) {
  const parts = useMemo(() => splitMessageMarkdown(content), [content]);
  return <>
    {parts.map((part, index) => part.kind === "table" ? (
      <div className="qg-table-scroll" role="region" tabIndex={0} aria-label="Response table" key={index}>
        <table><thead><tr>{part.headings.map((heading, cell) => <th scope="col" style={{ textAlign: part.align[cell] }} key={cell}><CellContent content={heading}/></th>)}</tr></thead>
          <tbody>{part.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, cell) => <td style={{ textAlign: part.align[cell] }} key={cell}><CellContent content={value}/></td>)}</tr>)}</tbody>
        </table>
      </div>
    ) : <ReactMarkdown key={index} components={{
      pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
      li: ({ children }) => <ListItem>{children}</ListItem>,
      p: ({ children }) => <p>{inlineStrike(children)}</p>,
      a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    }}>{part.value}</ReactMarkdown>)}
  </>;
}
