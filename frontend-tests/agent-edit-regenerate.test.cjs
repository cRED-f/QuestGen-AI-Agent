const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const page = fs.readFileSync(path.join(__dirname, '../app/(root)/chat/page.tsx'), 'utf8');

test('Agent Mode reuses the existing prompt editing composer and cancellation flow', () => {
  assert.match(page, /workspaceMode !== "chat" && workspaceMode !== "agent"/);
  assert.match(page, /\(message\.mode \|\| "chat"\) !== workspaceMode/);
  assert.match(page, /onClick=\{\(\) => editChatMessage\(message\)\}/);
  assert.match(page, /edit_message_id: editingMessageId/);
  assert.match(page, /onClick=\{cancelMessageEdit\}/);
  assert.match(page, /Later replies will be replaced/);
});

test('Agent Mode regenerates a persisted agent reply via streamed action, not the Chat endpoint', () => {
  assert.match(page, /\(workspaceMode === "chat" \|\| workspaceMode === "agent"\) && \(message\.mode \|\| "chat"\) === workspaceMode/);
  assert.match(page, /send\("", \{ regenerate_message_id: message\.id \}\)/);
  assert.match(page, /workspaceMode === "agent" \? "agent\/stream" : "stream"/);
  assert.match(page, /\.\.\.action,/);
  assert.match(page, /regenerate_message_id \? \[\] : attachedDocuments/);
  assert.match(page, /"started" && event\.user_id/);
  assert.match(page, /"done" && event\.message_id/);
});
