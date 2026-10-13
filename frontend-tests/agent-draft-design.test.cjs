const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const page = fs.readFileSync(path.join(__dirname, '../app/(root)/chat/page.tsx'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../app/globals.css'), 'utf8');

test('Agent question drafts stay in a chat-width tray with a bounded scrollable list', () => {
  const panel = css.match(/\.qg-agent-draft-bank \{([^}]+)\}/)?.[1] || '';
  assert.match(panel, /max-width:\s*790px/);
  assert.match(panel, /width:\s*calc\(100% - 60px\)/);
  assert.match(panel, /max-height:\s*min\(42dvh, 385px\)/);
  assert.match(panel, /overflow:\s*hidden/);
  assert.match(css, /\.qg-agent-question-list \{[^}]*overflow-y:\s*auto/);
  assert.match(css, /@media \(max-width: 680px\)\s*\{[^}]*\.qg-agent-draft-bank \{ width:\s*calc\(100% - 32px\)/);
});

test('Question previews are collapsed by default and open one at a time without losing actions', () => {
  assert.match(page, /useState<string \| null>\(null\);\s*const \[agentDraftBusy/);
  assert.match(page, /const expanded = agentExpandedDraftId === draft\.id/);
  assert.match(page, /aria-expanded=\{expanded\}/);
  assert.match(page, /setAgentExpandedDraftId\(current => current === draft\.id \? null : draft\.id\)/);
  assert.match(page, /\{expanded && <div id=\{`agent-draft-detail-\$\{draft\.id\}`\}/);
  assert.match(page, /updateAgentDraft\(draft, \{selected: event\.target\.checked\}\)/);
  assert.match(page, /updateAgentDraft\(draft, \{question: \{\.\.\.draft\.content, \.\.\.edits\}\}\)/);
  assert.match(page, /deleteAgentDraft\(draft\)/);
  assert.match(page, /formatSelectedInExam\(\)/);
  assert.match(page, /showAgentAnswers \? "Hide answers" : "Show answers"/);
});

test('Approved plan yields space to question review while pending plans remain visible', () => {
  assert.match(page, /agentPlan\.status === "pending" \|\| agentDrafts\.length === 0/);
  assert.match(page, /qg-agent-draft-bank--with-plan/);
  assert.match(css, /\.qg-agent-draft-bank--with-plan \{ max-height: min\(27dvh, 235px\); \}/);
  assert.match(css, /\.qg-agent-consult-plan--with-drafts \{ max-height: min\(24dvh, 200px\) !important; \}/);
});

test('The draft tray follows QuestGen paper-and-sage variables', () => {
  assert.match(css, /\.qg-agent-draft-bank \{[^}]*border: 1px solid var\(--qg-border\)/);
  assert.match(css, /background: var\(--qg-white\); color: var\(--qg-ink\)/);
  assert.match(css, /\.qg-agent-bank-title h2 \{[^}]*font-family: Georgia,serif/);
  assert.match(css, /\.qg-app\[data-theme="dark"\] \.qg-agent-draft-bank/);
});
