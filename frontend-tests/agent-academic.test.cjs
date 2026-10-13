const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const page = fs.readFileSync(path.join(__dirname, '../app/(root)/chat/page.tsx'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../app/globals.css'), 'utf8');

test('topic graph is retrieved for an existing agent conversation', () => {
  assert.match(page, /\/agent\/analysis/);
  assert.match(page, /setAcademicAnalysis\(analysis\)/);
  assert.match(page, /setAcademicAnalysis\(result\.analysis\)/);
});

test('coverage and citation labels are visible without implying full text scan', () => {
  assert.match(page, /Academic topic map/);
  assert.match(page, /passages analyzed/);
  assert.match(page, /analysis in progress/);
  assert.match(page, /topic\.evidence/);
});

test('professor receives optional contextual choices and retains composer input', () => {
  assert.match(page, /pending_question\.options/);
  assert.match(page, /setInput\(option\)/);
  assert.match(page, /You can write your own answer/);
  assert.match(css, /qg-agent-topic-map/);
  assert.match(css, /qg-agent-options/);
});
