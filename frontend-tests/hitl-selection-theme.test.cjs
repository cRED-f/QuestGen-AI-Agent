const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = relative => fs.readFileSync(path.join(__dirname, '..', relative), 'utf8');
const dropdown = read('components/workspace/ExamChoiceSelect.tsx');
const config = read('components/workspace/ExamConfiguration.tsx');
const builder = read('components/workspace/ExamPaperBuilder.tsx');
const css = read('app/globals.css');

test('all HITL selectors use the shared QuestGen dropdown rather than OS-native popup menus', () => {
  assert.match(config, /<ExamChoiceSelect label=\{`Question type/);
  assert.match(builder, /<ExamChoiceSelect label="Correct answer"/);
  assert.match(builder, /<ExamChoiceSelect label=\{`Difficulty for section/);
  assert.doesNotMatch(config, /<select\b/);
  assert.doesNotMatch(builder, /<select\b/);
});

test('dropdown can escape clipped HITL panel and its colors follow the warm and dark themes', () => {
  assert.match(dropdown, /createPortal\(/);
  assert.match(dropdown, /document\.body/);
  assert.match(dropdown, /role="combobox"/);
  assert.match(dropdown, /role="listbox"/);
  assert.match(dropdown, /aria-selected=\{option\.value === value\}/);
  assert.match(css, /\.qg-exam-choice-popover \{/);
  assert.match(css, /html\[data-theme="dark"\] \.qg-exam-choice-popover \{/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test('keyboard and pointer dismissal preserve normal selection behavior', () => {
  assert.match(dropdown, /"ArrowDown", "ArrowUp", "Enter", " "/);
  assert.match(dropdown, /event\.key === "Escape" \|\| event\.key === "Tab"/);
  assert.match(dropdown, /document\.addEventListener\("pointerdown", onPointerDown\)/);
  assert.match(dropdown, /window\.addEventListener\("scroll", onScroll, true\)/);
  assert.match(dropdown, /onChange\(choice\.value\)/);
});
