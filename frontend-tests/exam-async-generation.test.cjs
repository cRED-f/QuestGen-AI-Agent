const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const chat = fs.readFileSync(path.join(__dirname, '../app/(root)/chat/page.tsx'), 'utf8');
const config = fs.readFileSync(path.join(__dirname, '../next.config.ts'), 'utf8');

test('interactive exam generation starts a short job and polls for completion', () => {
  assert.match(chat, /\/exam\/paper\/generation\/start/);
  assert.match(chat, /\/exam\/paper\/generation\/\$\{start\.request_id\}/);
  assert.match(chat, /status\.status === "failed"/);
  assert.match(chat, /status\.status === "succeeded"/);
  assert.match(chat, /status\.error \|\| "Question generation failed/);
});

test('exam generation can reconnect on page reload and legacy rewrite has a longer timeout', () => {
  assert.match(chat, /\/exam\/paper\/generation\/latest/);
  assert.match(chat, /abort\?\.signal\.aborted/);
  assert.match(config, /experimental:\s*\{\s*proxyTimeout:\s*310_000\s*\}/);
});
