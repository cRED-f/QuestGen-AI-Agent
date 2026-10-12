const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = relative => fs.readFileSync(path.join(__dirname, '..', relative), 'utf8');
const config = read('components/workspace/ExamConfiguration.tsx');
const page = read('app/(root)/chat/page.tsx');
const css = read('app/globals.css');

test('Use this mix waits for the quick reply to save before closing editor', () => {
  assert.match(config, /const saved = await onChoose\(\{question_formats:/);
  assert.match(config, /if \(saved\) setCustom\(false\)/);
  assert.doesNotMatch(config, /void onChoose\(\{question_formats: formats\}/);
  assert.match(config, /savingMixRef\.current = true/);
  assert.match(config, /savingMix \? <>[\s\S]*Saving…/);
});

test('5 out of 5 is not silently accepted if a section row is invalid', () => {
  assert.match(config, /export function sectionMixError/);
  assert.match(config, /Number\.isInteger\(section\.count\)/);
  assert.match(config, /validMarks\(section\.marks_each, 100\)/);
  assert.match(config, /allocatedCents !== cents\(totalMarks\)/);
  assert.match(config, /\(mixError \|\| validationError\)[\s\S]*role="status"/);
  assert.match(css, /qg-exam-mix-feedback/);
});

test('backend save status is distinguished from secondary history refresh errors', () => {
  assert.match(page, /if \(!threadId \|\| examBusy \|\| busy \|\| !examIntroConfirmed\) return false/);
  assert.match(page, /return true;[\s\S]*?catch \(cause\) \{[\s\S]*?return false;/);
  assert.match(page, /void refreshThreads\(courseId\)\.catch/);
});

test('fractional mark values are supported consistently in the HITL editor', () => {
  assert.match(config, /min="0\.01" max="100" step="0\.01" value=\{format\.marks_each\}/);
  assert.match(config, /Math\.abs\(marks \* 100 - cents\(marks\)\) < 1e-7/);
  assert.match(config, /0\.01–100 marks per question/);
  assert.doesNotMatch(config, /Number\.isInteger\(section\.marks_each\)/);
});
