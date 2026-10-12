const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = (name) => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const page = read('app/(root)/chat/page.tsx');
const config = read('components/workspace/ExamConfiguration.tsx');
const css = read('app/globals.css');

test('paper preparation and question decisions render in the HITL region above the composer, not in chat', () => {
  const chatEnd = page.indexOf('          <div className="qg-compose-region">');
  const composerStart = page.indexOf('              <form className={`qg-composer', chatEnd);
  const builderPosition = page.indexOf('<ExamPaperBuilder');
  const configurationPosition = page.indexOf('<ExamConfiguration', chatEnd);
  assert.ok(chatEnd >= 0 && composerStart > chatEnd);
  assert.ok(builderPosition > configurationPosition && builderPosition < composerStart);
  assert.doesNotMatch(page.slice(0, chatEnd), /<ExamPaperBuilder/);
  assert.match(page.slice(configurationPosition, composerStart), /<\/ExamConfiguration>/);
});

test('HITL keeps paper controls approval-scoped and prevents double rendering', () => {
  assert.match(page, /examIntroConfirmed && examSession\.status === "approved" && <ExamPaperBuilder/);
  assert.equal((page.match(/<ExamPaperBuilder/g) || []).length, 1);
  assert.match(config, /children\?: ReactNode/);
  assert.match(config, /approved \? children : ready/);
  assert.match(config, /createPortal\(controls, controlsTarget\)/);
});

test('paper preview is scroll-bounded, theme-aware, and is not inserted into the transcript', () => {
  assert.match(css, /\.qg-exam-assistant--paper \{ max-height: min\(49vh, 470px\); overscroll-behavior: contain; \}/);
  assert.match(css, /\.qg-exam-assistant--paper \.qg-builder \{/);
  assert.match(css, /\.qg-app\[data-theme="dark"\] \.qg-exam-assistant--paper \.qg-builder/);
  assert.match(page, /<ExamPaperSidebar/);
});
