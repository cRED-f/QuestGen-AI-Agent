const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const read = location => fs.readFileSync(path.join(root, location), 'utf8');
const page = read('app/(root)/chat/page.tsx');
const sidebar = read('components/workspace/ExamPaperSidebar.tsx');
const dialog = read('components/workspace/PaperActionDialog.tsx');
const styles = read('app/globals.css');

test('both paper confirmations use a single QuestGen dialog, never the native window.confirm', () => {
  assert.match(page, /<PaperActionDialog\b/);
  assert.match(sidebar, /<PaperActionDialog\b/);
  assert.doesNotMatch(page, /window\.confirm\("Replace the existing Exam Mode paper/);
  assert.doesNotMatch(sidebar, /window\.confirm\("Approve the complete question paper/);
});

test('agent handoff asks before transfer, keeps the selected IDs, and requests second approval for server-confirmed replacement', () => {
  assert.match(page, /const openHandoffConfirmation = \(\) => \{/);
  assert.match(page, /setHandoffConfirmation\(\{ questionIds, threadId, replace: false \}\)/);
  assert.match(page, /onClick=\{openHandoffConfirmation\}/);
  assert.match(page, /confirmation\.questionIds, replace: confirmation\.replace/);
  assert.match(page, /message\.includes\("An exam paper already exists"\)/);
  assert.match(page, /setHandoffConfirmation\(\{ \.\.\.confirmation, replace: true \}\)/);
  assert.match(page, /setHandoffConfirmation\(null\);\s+setWorkspaceMode\("exam"\)/);
});

test('final paper approval reports failures and only closes the dialog after server success', () => {
  assert.match(sidebar, /if \(await onFinalApprove\(\)\) setApprovalOpen\(false\)/);
  assert.match(sidebar, /error=\{approvalError\}/);
  assert.match(page, /paperAction\("\/final-approve", "POST", undefined, \(\) => \{ approved = true; \}\)/);
});

test('dialog respects visual theme, uses a viewport portal, and supports keyboard/focus actions', () => {
  assert.match(dialog, /document\.querySelector<HTMLElement>\("\.qg-app"\)/);
  assert.match(dialog, /createPortal\(/);
  assert.match(dialog, /aria-modal="true"/);
  assert.match(dialog, /aria-labelledby=\{titleId\}/);
  assert.match(dialog, /aria-describedby=\{descriptionId\}/);
  assert.match(dialog, /event\.key === "Escape"/);
  assert.match(dialog, /event\.key !== "Tab"/);
  assert.match(dialog, /previousFocus\.current\?\.focus\(\)/);
  assert.match(styles, /\.qg-paper-action-dialog\s*\{/);
  assert.match(styles, /\.qg-app\[data-theme="dark"\] \.qg-paper-action-confirm/);
  assert.match(styles, /@media \(max-width: 460px\)/);
});
