const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const css = fs.readFileSync(path.join(__dirname, '../app/globals.css'), 'utf8');
const page = fs.readFileSync(path.join(__dirname, '../app/(root)/chat/page.tsx'), 'utf8');
const configuration = fs.readFileSync(path.join(__dirname, '../components/workspace/ExamConfiguration.tsx'), 'utf8');
const builder = fs.readFileSync(path.join(__dirname, '../components/workspace/ExamPaperBuilder.tsx'), 'utf8');
const motionSection = css.slice(css.indexOf('/* Fluid Morph:'));

test('composer morphs into confirmation with supported browser view transitions', () => {
  assert.match(page, /import \{ flushSync \} from "react-dom"/);
  assert.match(page, /startViewTransition\(\(\) => flushSync\(update\)\)/);
  assert.match(page, /morphComposer\(updateMode\)/);
  assert.match(page, /onClick=\{\(\) => morphComposer\(\(\) => \{/);
  assert.match(motionSection, /\.qg-composer, \.qg-exam-enter \{ view-transition-name: qg-compose-surface;/);
  assert.match(motionSection, /::view-transition-group\(qg-compose-surface\)/);
  assert.match(motionSection, /::view-transition-old\(root\), ::view-transition-new\(root\) \{ animation: none;/);
});

test('unsupported and reduced-motion browsers can switch without a transition', () => {
  assert.match(page, /reducedMotion \|\| typeof viewDocument\.startViewTransition !== "function"/);
  assert.match(page, /update\(\);\s+return;/);
  assert.match(motionSection, /@media \(prefers-reduced-motion: reduce\)/);
  const reduced = motionSection.slice(motionSection.indexOf('@media (prefers-reduced-motion: reduce)'));
  assert.match(reduced, /\.qg-mode-switch-indicator/);
  assert.match(reduced, /transition: none !important/);
  assert.match(reduced, /::view-transition-group\(qg-compose-surface\)/);
  assert.match(reduced, /animation: none !important/);
  assert.match(reduced, /\.qg-exam-prompt-reveal \{ animation: none !important; \}/);
});

test('active mode switch is a sliding indicator, never a bouncing scale', () => {
  assert.match(page, /className="qg-mode-switch" data-mode=\{workspaceMode\}/);
  assert.match(page, /className="qg-mode-switch-indicator" aria-hidden="true"/);
  assert.match(motionSection, /\.qg-mode-switch\[data-mode="exam"\] \.qg-mode-switch-indicator \{ transform: translateX\(100%\); \}/);
  assert.doesNotMatch(motionSection, /qg-motion-droplet|qg-motion-ripple|qg-motion-pill-pop|qg-motion-bubble-in/);
  assert.doesNotMatch(motionSection, /scale\(1\.0[1-9]/);
});

test('dialogs, materials, questions and sidebar use brief compositor-friendly entrances', () => {
  for (const selector of ['.qg-modal-backdrop', '.qg-material-overlay', '.qg-modal-backdrop > .qg-dialog',
    '.qg-material-overlay > .qg-document-panel', '.qg-pencil-menu', '.qg-paper-sidebar', '.qg-candidate']) {
    assert.ok(motionSection.includes(selector), `${selector} must have motion rules`);
  }
  const durations = [...motionSection.matchAll(/\b(\d+)ms\b/g)].map(match => Number(match[1]));
  assert.ok(durations.length > 0);
  assert.ok(durations.every(milliseconds => milliseconds <= 520), 'effects should finish within 520ms');
  assert.ok(!motionSection.includes('filter: blur'), 'avoid animating blur filters');
});

test('prompt and candidates animate only after identity changes', () => {
  assert.match(configuration, /className="qg-exam-assistant-body qg-exam-prompt-reveal"/);
  assert.match(builder, /<div key=\{paper\.candidate_id \?\?/);
});
