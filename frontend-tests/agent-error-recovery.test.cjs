const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const page = fs.readFileSync(path.join(root, 'app/(root)/chat/page.tsx'), 'utf8');
const app = fs.readFileSync(path.join(root, 'backend/app.py'), 'utf8');

test('unsent Agent Mode instructions are restored when a request fails', () => {
  assert.match(page, /workspaceMode === "agent" && !action && !controller\.signal\.aborted/);
  assert.match(page, /setInput\(previous => previous\.trim\(\) \? previous : content\)/);
});

test('FastAPI returns a reference code and stage-specific agent error', () => {
  assert.match(app, /X-QuestGen-Error-Reference/);
  assert.match(app, /status_code=fault\.status_code/);
  assert.match(app, /stage: \{fault\.stage\}; reference: \{reference\}/);
});
