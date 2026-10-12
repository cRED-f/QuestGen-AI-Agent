const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const page = read('app/(root)/chat/page.tsx');
const css = read('app/globals.css');
const config = read('components/workspace/ExamConfiguration.tsx');

test('the professor acknowledges exam mode only once per conversation', () => {
  assert.match(page, /questgen:exam-intro:\$\{id\}/);
  assert.match(page, /window\.localStorage\.setItem\(examIntroKey\(threadId\), "true"\)/);
  assert.match(page, /setExamIntroConfirmed\(introAlreadyConfirmed\(last\)\)/);
  assert.match(page, /setExamIntroConfirmed\(introAlreadyConfirmed\(id\)\)/);
  assert.match(page, /const nextConfirmed = mode === "exam" \? introAlreadyConfirmed\(threadId\)/);
});

test('mode changes preserve messages and their on-screen position', () => {
  assert.match(page, /captureConversationAnchor\(\)/);
  assert.match(page, /useLayoutEffect\(\(\) => \{/);
  assert.match(page, /data-message-id=\{message.id\}/);
  assert.match(page, /followConversationRef\.current/);
  assert.match(page, /flushSync\(updateMode\)/);
  assert.doesNotMatch(css, /\.qg-chat-column--exam \.qg-chat-frame \{ animation:/);
});

test('dark mode is a saved user setting, using a muted rather than black palette', () => {
  assert.match(page, /questgen:theme/);
  assert.match(page, /aria-pressed=\{theme === "dark"\}/);
  assert.match(page, /chooseTheme\("dark"\)/);
  assert.match(css, /\.qg-app\[data-theme="dark"\]/);
  assert.match(css, /--qg-paper: #252f2a/);
  assert.doesNotMatch(css, /--qg-paper: #000/);
});

test('exam prompts use sidebar color and the exact QuestGen icon', () => {
  assert.match(css, /\.qg-exam-assistant \{\s*background: var\(--qg-sidebar\)/);
  assert.match(config, /<img src="\/icon.svg"/);
  assert.match(page, /qg-exam-enter-icon"><img src="\/icon.svg"/);
});

test('readme displays the project icon at the top', () => {
  assert.match(read('README.md').slice(0,250), /<img src="app\/icon.svg"/);
});
