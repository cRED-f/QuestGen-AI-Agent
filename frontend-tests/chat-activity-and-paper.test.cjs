const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const page = read('app/(root)/chat/page.tsx');
const config = read('components/workspace/ExamConfiguration.tsx');
const sidebar = read('components/workspace/ExamPaperSidebar.tsx');
const css = read('app/globals.css');

test('human-in-the-loop prompt remains a message but controls portal to the composer surface', () => {
  assert.match(config, /qg-exam-prompt-bubble/);
  assert.match(config, /createPortal\(controls, controlsTarget\)/);
  assert.match(config, /approved \? children : ready/);
  assert.match(config, /Use this mix/);
  assert.match(page, /controlsTarget=\{hitlActionsTarget\}/);
  assert.match(page, /ref=\{setHitlActionsTarget\}/);
  assert.ok(page.indexOf('ref={setHitlActionsTarget}') < page.indexOf('<form className={`qg-composer'));
  assert.match(css, /\.qg-hitl-composer-actions \+/);
});

test('scroll follows new messages but shows an accessible latest button for readers who scroll up', () => {
  assert.match(page, /new ResizeObserver/);
  assert.match(page, /followConversationRef\.current/);
  assert.match(page, /setShowScrollDown\(distance > 145\)/);
  assert.match(page, /aria-label="Scroll to latest message"/);
  assert.match(page, /\{showScrollDown && <button[^\n]*><ArrowDown size=\{19\} aria-hidden="true"\/><\/button>/);
  assert.doesNotMatch(page, /<span>Latest<\/span>/);
  assert.match(css, /\.qg-scroll-latest \{[^}]*left: 50%/);
  assert.match(css, /\.qg-scroll-latest \{[^}]*transform: translateX\(-50%\)/);
  assert.match(css, /\.qg-scroll-latest:hover \{ transform: translate\(-50%, -2px\)/);
  assert.match(page, /scrollToLatest\(true\)/);
  assert.match(page, /prefers-reduced-motion: reduce/);
});

test('activity reports estimated visible output only and is collapsible', () => {
  assert.match(page, /assistantActivity/);
  assert.match(page, /Math\.ceil\(\(stats\?\.visibleChars/);
  assert.match(page, /aria-expanded=\{expanded\}/);
  assert.match(page, /reasoningText && <div className="qg-ai-reasoning">/);
  assert.match(page, /output tokens/);
  assert.match(page, /Generating response/);
});

test('exam paper uses editor theme, accessible progress, and compact status cards', () => {
  assert.match(sidebar, /qg-paper-studio-stats/);
  assert.match(sidebar, /aria-label="Paper completion"/);
  assert.match(sidebar, /qg-paper-studio-header/);
  assert.match(css, /\.qg-paper-sidebar \{ background: var\(--qg-sidebar\)/);
  assert.match(css, /\.qg-app\[data-theme="dark"\] \.qg-paper-sidebar/);
});

test('backend errors identify failing routes and can retry transient refresh reads', () => {
  assert.match(page, /\[500, 502, 503, 504\]/);
  assert.match(page, /Check both the Next\.js and FastAPI terminals/);
  assert.match(page, /workspaceMode === "exam"\) \{ setAssessment\(null\)/);
});
