#!/usr/bin/env node
/** Real 7011 semantic drill-downs for supplemental read-only product tools. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { chromium } from 'playwright';
import { capabilityAvailable } from './tool_followup_oracle.mjs';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = 'sft_alex_creator';
const fixtureMarker = `contact-${crypto.randomUUID()}`;
const fixtureEmail = `${fixtureMarker}@example.test`;
const fixturePhone = `+1-202-555-0142 ext ${Date.now()}`;
const fixtureAddress = '42 Fixture Lane';
const skillName = `ref-${crypto.randomUUID()}`;
const skillDir = path.join((process.env.ODYSSEUS_SKILLS_ROOT || path.join(root, "data", "skills", "general")), skillName);
const referenceText = '# Recovery reference\n\nRetry ceiling: 7 attempts.\nWait between attempts: 13 seconds.\nStop marker: violet-72.\n';
const selected = new Set((process.env.CASES || '').split(',').filter(Boolean));
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/supplemental-read-drilldowns-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const cases = [
  {
    name: 'skill-reference-recovery', tool: 'manage_skills', capability: 'skills', skillFixture: true,
    prompts: [
      `Show the full SKILL.md for my skill ${skillName}. Only read that file, not its supporting references yet.`,
      'Now read its references/recovery.md and tell me the retry ceiling, wait between attempts, and stop marker.',
      'What was the wait between attempts again? Do not change anything.',
      'Read references/missing.md under that same skill. Report whether you could read it; do not substitute another file.',
      'Sorry, I meant references/recovery.md in the same skill. What is its stop marker?',
    ],
    validate: (index, args) => args.name === skillName && (index === 0 ? args.action === 'view'
      : args.action === 'view_ref' && args.path === (index === 3 ? 'references/missing.md' : 'references/recovery.md')),
  },
  {
    name: 'contact-phone-address-followup', tool: 'manage_contact', capability: 'contacts', fixture: true,
    prompts: [`Find my contact named ${fixtureMarker}. Read only.`, 'What is their phone number and street address?'],
    // Listing and identifying the requested contact is also valid retrieval;
    // the source and final-answer checks below establish the actual identity.
    validate: (index, args) => ['list', 'search', 'find'].includes(args.action),
  },
  {
    name: 'research-list-open-second', tool: 'manage_research', capability: 'research',
    prompts: ['List my saved research reports. Return at most three titles. Read only.', 'Open the second saved research report from that list and summarize it. Read only.'],
    validate: (index, args) => index === 0 ? args.action === 'list' : ['read', 'open', 'view', 'get'].includes(args.action) && typeof args.id === 'string' && args.id.length > 0,
  },
  {
    name: 'sessions-list-filter', tool: 'list_sessions', capability: 'sessions',
    prompts: ['List my chat sessions. Return at most three titles. Read only.', 'Filter that same chat list to titles containing audit. Read only.'],
    validate: (index, args) => index === 0 ? !args.filter : typeof args.filter === 'string' && /audit/i.test(args.filter),
  },
  {
    name: 'contacts-list-search', tool: 'manage_contact', capability: 'contacts',
    prompts: ['List my contacts. Return at most three names. Read only.', 'Now search those contacts for Casey. Read only.'],
    validate: (index, args) => index === 0 ? args.action === 'list' : ['search', 'find'].includes(args.action) && /casey/i.test(String(args.query || args.name || '')),
  },
].filter(spec => !selected.size || selected.has(spec.name));
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { model, routing: 'recent_model_choice', status: 'running', cases: [], privacy: 'No report bodies, chat titles, contact data, tool output, identifiers, or answer text retained.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const parseArgs = event => { try { return JSON.parse(event?.command || '{}'); } catch { return {}; } };

let browser, context, page;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: {
    'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': 'recent_model_choice',
  } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  for (const spec of cases) {
    const result = { name: spec.name, expected_tool: spec.tool, turns: [], cleanup: false, status: 'running' };
    report.cases.push(result); save();
    let session = '';
    try {
      if (spec.skillFixture) {
        if (fs.existsSync(skillDir)) throw Error('Skill fixture already exists');
        const seeded = await context.request.post(`${base}/api/skills/add`, {data: {
          name: skillName, description: 'Disposable reference-reading fixture', category: 'general', status: 'draft',
          procedure: ['Consult references/recovery.md for recovery parameters.'], verification: ['Quote the reference values.'],
        }});
        if (!seeded.ok()) throw Error('Skill fixture creation failed');
        const row = (await seeded.json()).skill;
        if (row?.name !== skillName || row?.owner !== owner || row?.status !== 'draft') throw Error('Skill fixture identity mismatch');
        if (fs.realpathSync(skillDir) !== skillDir) throw Error('Unexpected skill fixture path');
        fs.mkdirSync(path.join(skillDir, 'references'));
        fs.writeFileSync(path.join(skillDir, 'references/recovery.md'), referenceText, {flag: 'wx'});
        result.fixture_verified = true;
      }
      if (spec.fixture) {
        const seeded = await context.request.post(`${base}/api/contacts/add`, {data: {
          name: fixtureMarker, email: fixtureEmail, phones: [fixturePhone], address: fixtureAddress,
        }});
        if (!seeded.ok() || !(await seeded.json()).success) throw Error('Fixture contact creation failed');
        const rows = (await (await context.request.get(`${base}/api/contacts/list`)).json()).contacts || [];
        result.fixture_verified = rows.some(row => row.owner === owner && row.name === fixtureMarker
          && row.emails?.includes(fixtureEmail) && row.phones?.includes(fixturePhone) && row.address === fixtureAddress);
        if (!result.fixture_verified) throw Error('Fixture contact state mismatch');
      }
      const created = await context.request.post(`${base}/api/session`, { multipart: {
        name: `[supplemental-read-drilldown] ${spec.name}`, model, endpoint_id: endpointId,
        endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
      }});
      if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
      session = (await created.json()).id;
      page = await context.newPage();
      await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
      const agent = page.locator('#mode-agent-btn');
      if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
      let researchIds = [];
      let referenceRead = false;
      for (let index = 0; index < spec.prompts.length; index++) {
        if (spec.tool === 'manage_research' && index === 1 && researchIds.length < 2) {
          throw Error('PRECONDITION: fewer than two saved reports returned; second-report resolution not testable');
        }
        const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
        await page.locator('textarea#message:visible').fill(spec.prompts[index]);
        await page.locator('textarea#message:visible').press('Enter');
        const response = await waiting;
        const events = parseSSE(await response.text());
        await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 15000 }).catch(() => {});
        const contract = events.find(event => event.type === 'turn_contract') || {};
        const starts = events.filter(event => event.type === 'tool_start');
        const outputs = events.filter(event => event.type === 'tool_output' && event.tool === spec.tool);
        const expected = starts.filter(event => event.tool === spec.tool);
        const args = parseArgs(expected[0]);
        const reusedEvidence = Boolean(starts.length === 0 && ((spec.fixture && index === 1)
          || (spec.skillFixture && [2, 4].includes(index) && referenceRead)));
        const final = events.filter(e => e.type === 'final_response').map(e => e.content || '').join('')
          || events.filter(e => typeof e.delta === 'string').map(e => e.delta).join('');
        if (spec.tool === 'manage_research' && index === 0) {
          researchIds = [...String(outputs[0]?.output || '').matchAll(/— id: ([^\s]+)/g)].map(match => match[1]);
        }
        const source = outputs.map(e => String(e.output || '')).join('\n');
        const expectedFailure = spec.skillFixture && index === 3;
        const skillAnswerMatches = text => !spec.skillFixture || (index === 0 ? text.includes('references/recovery.md')
          : index === 1 ? /\b7\b/.test(text) && /\b13\b/.test(text) && text.includes('violet-72')
          : index === 2 ? /\b13\b/.test(text) && /second/i.test(text)
          : index === 3 ? /not found|could(?:n.t| not)|unavailable|does(?:n.t| not) exist|unable|missing/i.test(text)
          : text.includes('violet-72'));
        const skillAnswer = skillAnswerMatches(final);
        const displayed = await page.locator('#chat-history .msg-ai .stream-content').last().innerText({timeout: 5000}).catch(() => '');
        const checks = {
          http_ok: response.ok(), clean_route: contract.selection_mode === 'clean_compact_v3_preview',
          model_choice_route: contract.routing_experiment === 'recent_model_choice',
          capability: capabilityAvailable(contract, spec.capability, [spec.tool]),
          expected_offered: (contract.offered || []).includes(spec.tool),
          exactly_one_expected_call: reusedEvidence || (starts.length === 1 && expected.length === 1),
          argument_contract: reusedEvidence || (expected.length === 1 && spec.validate(index, args)),
          exact_research_reference: spec.tool !== 'manage_research' || index === 0 || args.id === researchIds[1],
          expected_execution_outcome: reusedEvidence || (outputs.length === 1 && (expectedFailure
            ? Boolean(outputs[0]?.error || outputs[0]?.exit_code === 1)
            : !outputs[0]?.error && (outputs[0]?.exit_code == null || outputs[0]?.exit_code === 0))),
          skill_reference_source: !spec.skillFixture || ![1, 2, 4].includes(index) || (reusedEvidence
            ? referenceRead : source.includes('Retry ceiling: 7 attempts.') && source.includes('Wait between attempts: 13 seconds.') && source.includes('Stop marker: violet-72.')),
          skill_answer_evidence: skillAnswer,
          rendered_answer_nonempty: displayed.trim().length > 0,
          rendered_skill_evidence: skillAnswerMatches(displayed),
          skill_fixture_unchanged: !spec.skillFixture || fs.readFileSync(path.join(skillDir, 'references/recovery.md'), 'utf8') === referenceText,
          fixture_evidence: !spec.fixture || (index === 0
            ? outputs.some(e => String(e.output || '').includes(fixturePhone) && String(e.output || '').includes(fixtureAddress))
            : final.replace(/\D/g, '').includes(fixturePhone.replace(/\D/g, '')) && final.toLowerCase().includes(fixtureAddress.toLowerCase())),
          fixture_identity: !spec.fixture || index !== 0 || final.includes(fixtureMarker) || final.includes(fixtureEmail),
          no_stream_error: !events.some(event => ['error', 'invalid_sse'].includes(event.type)),
        };
        if (spec.skillFixture && index === 0 && !skillAnswer) {
          // Only the disposable skill's failed answer, never general read data.
          console.log(JSON.stringify({fixture_diagnostic: 'skill-body-answer', answer: final.slice(0, 1000),
            rendered_answer: displayed.slice(0, 1000),
            event_types: [...new Set(events.map(e => e.type))]}));
        }
        if (spec.skillFixture && args.action === 'view_ref' && args.path === 'references/recovery.md'
            && checks.argument_contract && checks.expected_execution_outcome && checks.skill_reference_source) referenceRead = true;
        result.turns.push({ index, tools: starts.map(event => event.tool), action: args.action || null,
          argument_keys: Object.keys(args).sort(), checks, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed',
          ...(spec.skillFixture ? {calls: expected.map(event => {
            const a = parseArgs(event);
            return {action: a.action, name_is_fixture: a.name === skillName, keys: Object.keys(a).sort(),
              path: ['references/recovery.md', 'references/missing.md'].includes(a.path) ? a.path : a.path ? 'other' : null};
          }), successful_outputs: outputs.filter(e => !e.error && (!e.exit_code || e.exit_code === 0)).length} : {}),
        });
        save();
      }
      result.status = result.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
    } catch (error) {
      result.error = String(error).split('\n')[0].slice(0, 400); result.status = 'failed';
      result.precondition_failure = result.error.includes('PRECONDITION:');
    } finally {
      if (page) { await page.close(); page = null; }
      if (spec.skillFixture) {
        try {
          const found = await context.request.get(`${base}/api/skills/${encodeURIComponent(skillName)}`);
          if (found.ok()) {
            const row = await found.json();
            if (row.name !== skillName || row.owner !== owner) throw Error('Refuse non-fixture cleanup');
            const removed = await context.request.delete(`${base}/api/skills/${encodeURIComponent(skillName)}`);
            if (!removed.ok()) throw Error('Skill fixture delete failed');
          }
          result.fixture_cleanup = (await context.request.get(`${base}/api/skills/${encodeURIComponent(skillName)}`)).status() === 404
            && !fs.existsSync(skillDir);
        } catch { result.fixture_cleanup = false; }
        if (!result.fixture_cleanup) result.status = 'failed';
      }
      if (spec.fixture) {
        try {
          const rows = (await (await context.request.get(`${base}/api/contacts/list`)).json()).contacts || [];
          for (const row of rows.filter(row => row.owner === owner && row.name === fixtureMarker && row.emails?.includes(fixtureEmail))) {
            const removed = await context.request.delete(`${base}/api/contacts/${encodeURIComponent(row.uid)}`);
            if (!removed.ok() || !(await removed.json()).success) throw Error('Fixture delete failed');
          }
          const remaining = (await (await context.request.get(`${base}/api/contacts/list`)).json()).contacts || [];
          result.fixture_cleanup = !remaining.some(row => row.owner === owner && row.emails?.includes(fixtureEmail));
        } catch { result.fixture_cleanup = false; }
        if (!result.fixture_cleanup) result.status = 'failed';
      }
      if (session) result.cleanup = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
      if (!result.cleanup) result.status = 'failed';
      save();
    }
  }
} catch (error) {
  report.error = String(error).split('\n')[0].slice(0, 400);
} finally {
  if (page) await page.close();
  if (browser) await browser.close();
}
report.status = report.cases.length === cases.length && report.cases.every(item => item.status === 'passed') ? 'passed' : 'failed';
report.summary = { passed: report.cases.filter(item => item.status === 'passed').length, total: cases.length, turns: report.cases.reduce((sum, item) => sum + item.turns.length, 0) };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary, failures: report.cases.filter(item => item.status !== 'passed') }));
if (report.status !== 'passed') process.exitCode = 1;
