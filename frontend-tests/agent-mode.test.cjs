const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const page = fs.readFileSync(path.join(__dirname, '../app/(root)/chat/page.tsx'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../app/globals.css'), 'utf8');

test('Agent is a separately persisted mode with its own request handler', () => {
  assert.match(page, /"chat" \| "exam" \| "agent"/);
  assert.match(page, /switchMode\("agent"\)/);
  assert.match(page, /\/agent\/turn/);
  assert.match(page, /\/agent\/status/);
  assert.match(page, /mode === "agent" \? agentDraftRef\.current/);
  assert.match(page, /} else if \(workspaceMode === "agent"\) \{/);
});

test('User responses to waiting professor questions go through the shared composer', () => {
  assert.match(page, /agentStatus\.pending_question\.question/);
  assert.match(page, /Reply in the message box to resume this investigation/);
  assert.match(page, /setAgentStatus\(result\.state\)/);
  assert.match(page, /document_ids: chosenDocuments\.map\(doc => doc\.id\)/);
});

test('Third mode is responsive and styled for existing themes', () => {
  assert.match(css, /grid-template-columns: repeat\(3/);
  assert.match(css, /qg-mode-switch\[data-mode="agent"\]/);
  assert.match(css, /qg-agent-context/);
  assert.match(css, /@media\(max-width: 780px\)/);
});
