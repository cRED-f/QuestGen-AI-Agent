// Preserve the exact message text (including Markdown) when copying either role.
// A legacy fallback supports browser contexts without the async Clipboard API.
export async function writeMessageToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Browsers may block the async API in insecure contexts; try selection copy.
    }
  }

  const textarea = document.createElement("textarea");
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  textarea.value = text;
  textarea.readOnly = true;
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  try {
    textarea.focus();
    textarea.select();
    if (!document.execCommand("copy")) throw new Error("Clipboard copy was rejected.");
  } finally {
    textarea.remove();
    previousFocus?.focus({ preventScroll: true });
  }
}

