const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8');

test('Agent Mode announces keyless DuckDuckGo research', () => {
  const page = read('app/(root)/chat/page.tsx');
  assert.match(page, /Free DuckDuckGo search is enabled \(no API key\)/);
  assert.match(page, /Install the backend dependencies \(including ddgs\)/);
  assert.doesNotMatch(page, /Tavily or Brave Search API key/);
});

test('The backend uses DDGS with the DuckDuckGo engine rather than key-based search', () => {
  const research = read('backend/agent/research.py');
  assert.match(research, /from ddgs import DDGS/);
  assert.match(research, /backend='duckduckgo'/);
  assert.doesNotMatch(research, /TAVILY_API_KEY|BRAVE_SEARCH_API_KEY/);
  assert.match(read('backend/requirements.txt'), /ddgs>=9\.16,<10/);
});
