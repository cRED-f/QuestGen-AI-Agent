const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/(root)/chat/page.tsx'), 'utf8');
const backend = fs.readFileSync(path.join(__dirname, '../backend/agent_mode.py'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../app/globals.css'), 'utf8');

test('Agent Mode shows reviewable dynamic plan with deliberate generation approval', () => {
  assert.match(source, /Approve plan & draft questions/);
  assert.match(source, /agentPlan\.plan\.details\.map/);
  assert.match(source, /refreshAgentPlan\(activeId\)/);
  assert.match(source, /generate_from_plan: true/);
  assert.match(source, /plan: AgentPlan|plan\?: AgentPlan/);
});

test('Agent loop has no app-level step or tool call ceiling', () => {
  assert.doesNotMatch(backend, /MAX_STEPS\s*=/);
  assert.doesNotMatch(backend, /MAX_TOOL_CALLS\s*=/);
  assert.match(backend, /while True:/);
  assert.match(backend, /MAX_WEB_PAGES = 3/);
});


test('Consultation plan matches the QuestGen surface with compact, accessible disclosure', () => {
  assert.match(source, /<details key=\{`\$\{agentPlan\.thread_id\}-\$\{agentPlan\.version\}`\} className="qg-agent-plan-disclosure"/);
  assert.match(source, /<summary>/);
  assert.match(source, /qg-agent-plan-status/);
  assert.match(source, /qg-agent-plan-guidance/);
  assert.match(css, /\.qg-agent-consult-plan \{/);
  assert.match(css, /background: var\(--qg-white\)/);
  assert.match(css, /color: var\(--qg-ink\)/);
  assert.match(css, /\.qg-agent-plan-details > div \{/);
  assert.match(css, /\.qg-agent-plan-disclosure\[open\]/);
  assert.match(css, /max-height: min\(55dvh, 410px\)/);
  assert.doesNotMatch(css, /\.qg-agent-plan-details\s*\{[^}]*grid-template-columns: repeat\(auto-fit/);
});
