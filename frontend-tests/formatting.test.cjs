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

test('flattened Tina and David question reconstructs its six-row table and preserves the marks', () => {
  const text = 'Tina and David produce coconuts and pineapples. Which producer has comparative advantage? | Producer | Coconuts | Pineapples | |---|---|---| | Tina | 100 | 0 | | Tina | 50 | 25 | | Tina | 0 | 50 | | David | 50 | 0 | | David | 25 | 40 | | David | 0 | 80 | **(5)**';
  const result = parse(text);
  assert.equal(result.length, 3);
  assert.match(result[0].value, /Which producer/);
  assert.equal(result[0].value.includes('|---|'), false);
  const table = result[1];
  assert.equal(table.kind, 'table');
  assert.deepEqual(Array.from(table.headings), ['Producer', 'Coconuts', 'Pineapples']);
  assert.deepEqual(Array.from(table.rows, row => Array.from(row)), [
    ['Tina','100','0'], ['Tina','50','25'], ['Tina','0','50'],
    ['David','50','0'], ['David','25','40'], ['David','0','80'],
  ]);
  assert.match(result[2].value, /\*\*\(5\)\*\*/);
});

test('ordinary prose and fenced code with flattened-looking table are left untouched', () => {
  const flattened = 'Example | A | B | |---|---| | one | two |';
  assert.equal(parse(`\`\`\`md\n${flattened}\n\`\`\``).length, 1);
  const prose = 'The author says A | B, with no table separator.';
  assert.equal(parse(prose).length, 1);
  assert.equal(parse(prose)[0].value, prose);
});

test('formatted question stems are used in every exam surface', () => {
  for (const file of ['ExamPaperBuilder.tsx', 'ExamPaperSidebar.tsx', 'QuestionEditor.tsx']) {
    const contents = fs.readFileSync(path.join(__dirname, `../components/workspace/${file}`), 'utf8');
    assert.match(contents, /<FormattedMessage content=\{(?:candidate\.stem|question\.content\.stem|q\.content\.stem)\}/);
  }
});
