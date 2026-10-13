const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const page = fs.readFileSync(path.join(__dirname, '../app/(root)/chat/page.tsx'), 'utf8');
const configuration = fs.readFileSync(path.join(__dirname, '../components/workspace/ExamConfiguration.tsx'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../app/globals.css'), 'utf8');

test('model settings sidebar does not expose the provider/model string', () => {
  assert.match(page, /<span className="qg-settings-copy"><strong>Model settings<\/strong><\/span>/);
  assert.doesNotMatch(page, /qg-settings-copy[^\n]+settings\.model/);
});

test('entering Exam Mode hides composer until professor confirms', () => {
  assert.match(page, /workspaceMode === "exam" && !examIntroConfirmed \? \(/);
  assert.match(page, /Start with QuestGen/);
  assert.match(page, /examIntroKey\(threadId\)/);
  assert.match(page, /setExamIntroConfirmed\(true\)/);
  assert.match(page, /: <>\s*\{workspaceMode === "exam" && examSession && <ExamConfiguration/);
  assert.match(page, /&& examSession && <ExamConfiguration/);
  assert.match(page, /<form className=\{`qg-composer/);
});

test('clarification is a conversation prompt just above composer, not a seven-step form', () => {
  assert.match(page, /<ExamConfiguration[\s\S]*?<form className=\{`qg-composer/);
  assert.doesNotMatch(configuration, /qg-exam-progress-track|qg-exam-review-grid|<form key=/);
  assert.match(configuration, /qg-exam-quick-replies/);
  assert.match(configuration, /qg-exam-confirm-details/);
  assert.match(page, /\/exam\/quick-reply/);
});

test('mode confirmation uses a fluid surface reveal and respects reduced motion', () => {
  assert.match(css, /\.qg-exam-enter \{/);
  assert.match(css, /\.qg-exam-prompt-reveal \{/);
  assert.match(css, /@media\(prefers-reduced-motion: reduce\)/);
  assert.match(css, /\.qg-exam-enter, \.qg-exam-empty, \.qg-exam-assistant, \.qg-exam-prompt-reveal \{ animation: none !important; \}/);
});
