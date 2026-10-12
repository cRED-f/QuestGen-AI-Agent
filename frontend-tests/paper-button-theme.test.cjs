const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const css = fs.readFileSync(path.join(__dirname, '..', 'app/globals.css'), 'utf8');

test('paper building primary actions follow the QuestGen palette in both themes', () => {
  assert.match(css, /\.qg-paper-primary \{ color: #fbfaf6 !important; background: #303530 !important; border-color: #303530 !important; \}/);
  assert.match(css, /\.qg-paper-primary:not\(:disabled\):hover \{ background-color: #455044 !important; border-color: #455044 !important; \}/);
  assert.match(css, /\.qg-app\[data-theme="dark"\] \.qg-paper-primary \{ color: #f6f3e9 !important; background: #4d6454 !important; border-color: #607761 !important; \}/);
  assert.match(css, /\.qg-paper-primary:focus-visible \{ outline: 2px solid var\(--qg-pencil\); outline-offset: 3px; \}/);
});
