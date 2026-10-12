const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
const page = read('app/(root)/chat/page.tsx');
const builder = read('components/workspace/ExamPaperBuilder.tsx');
const css = read('app/globals.css');
const batch = read('backend/exam_batch.py');

test('flagged questions use the existing composer, not a second revision textarea', () => {
  assert.doesNotMatch(builder, /revisionInstructions|qg-batch-revision|onReviseBatch/);
  assert.match(page, /const revisingSelection = revisionTargets\.length > 0/);
  assert.match(page, /revisingSelection \? revisionInput : input/);
  assert.match(page, /className="qg-revision-compose-context"/);
  assert.match(page, /revisionTargets\.slice\(0, 6\)\.map/);
  assert.match(page, /if \(revisionSelectionKey\) textRef\.current\?\.focus\(\)/);
});

test('Enter and Send submit scoped batch revision instead of a chat turn', () => {
  const sendFromComposer = page.slice(page.indexOf('const sendFromComposer ='), page.indexOf('const onSubmit ='));
  assert.match(sendFromComposer, /if \(revisingSelection\)/);
  assert.match(sendFromComposer, /paperAction\("\/batch\/revise", "POST"/);
  assert.match(sendFromComposer, /draft_ids: revisionTargets\.map\(draft => draft\.id\)/);
  assert.match(sendFromComposer, /instructions: revisionInput\.trim\(\)/);
  assert.match(sendFromComposer, /setRevisionInput\(""\)/);
  assert.doesNotMatch(sendFromComposer, /\/exam\/turn/);
  assert.match(page, /disabled=\{\(!revisingSelection && !input\.trim\(\)\)/);
  assert.match(page, /maxLength=\{revisingSelection \? 1200 : 12000\}/);
});

test('revision is reversible and visual layout respects the window and dark theme', () => {
  assert.match(batch, /Literal\['pending', 'keep', 'revise'\]/);
  assert.match(builder, /draft\.decision === "revise" \? "pending" : "revise"/);
  assert.match(builder, /qg-batch-draft--revise/);
  assert.match(css, /qg-hitl-composer-actions:has\(\.qg-builder--batch\)/);
  assert.match(css, /qg-app\[data-theme="dark"\] \.qg-revision-compose-context/);
  assert.match(css, /@media \(max-width: 600px\)/);
});
