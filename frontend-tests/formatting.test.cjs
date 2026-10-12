/* Independent parser checks; no browser or API credentials required. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../components/workspace/FormattedMessage.tsx'), 'utf8');
const js = ts.transpileModule(source, {
  fileName: 'FormattedMessage.tsx',
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const context = { exports: {}, require: () => ({}) };
vm.runInNewContext(js, context, { filename: 'FormattedMessage.js' });
const parse = context.exports.splitMessageMarkdown;

test('pipe tables produce headings, alignment and rows', () => {
  const result = parse('Before\n\n| Name | Grade |\n| :--- | ---: |\n| **Ada** | 9 |\n| Bob | 7 |\n\nAfter');
  const table = result.find((part) => part.kind === 'table');
  assert.deepEqual(Array.from(table.headings), ['Name', 'Grade']);
  assert.deepEqual(Array.from(table.align), ['left', 'right']);
  assert.equal(table.rows.length, 2);
  assert.equal(table.rows[0][0], '**Ada**');
  assert.equal(result[0].value.includes('Before'), true);
  assert.equal(result.at(-1).value.includes('After'), true);
});

test('pipes inside fenced code are never parsed into a table', () => {
  const result = parse('```md\n| A | B |\n| --- | --- |\n| 1 | 2 |\n```');
  assert.equal(result.length, 1);
  assert.equal(result[0].kind, 'markdown');
});

test('escaped pipes and inline code pipes remain in the same cell', () => {
  const result = parse('| Before | After |\n| --- | --- |\n| `x|y` | a\\|b |');
  assert.equal(result[0].kind, 'table');
  assert.equal(result[0].rows[0][0], '`x|y`');
  assert.equal(result[0].rows[0][1], 'a\\|b');
});

test('unrelated text containing pipes is not rendered as a table', () => {
  const result = parse('Choice A | Choice B\nNot a delimiter\n\nSome other text.');
  assert.equal(result.length, 1);
  assert.equal(result[0].kind, 'markdown');
});
