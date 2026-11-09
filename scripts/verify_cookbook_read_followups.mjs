#!/usr/bin/env node
/** Real 7011 Agent UI replay for every clean-preview Cookbook read surface. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = 'sft_alex_creator';
const routingMode = 'recent_model_choice';
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/cookbook-read-followups-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const selected = new Set((process.env.CASES || '').split(',').map(value => value.trim()).filter(Boolean));
let cases = [
  ['model-catalog', 'list_models', 'List available models. Read only.', 'Refresh that same model catalog list. Read only.'],
  ['cached-models', 'list_cached_models', 'List locally cached models. Read only.', 'Refresh that same cached-model list. Read only.'],
  ['served-models', 'list_served_models', 'List served models. Read only.', 'Refresh that same served-model list. Read only.'],
  ['downloads', 'list_downloads', 'List downloads. Read only.', 'Refresh that same downloads list. Read only.'],
  ['serve-presets', 'list_serve_presets', 'List serve presets. Read only.', 'Refresh that same serve-preset list. Read only.'],
  ['cookbook-servers', 'list_cookbook_servers', 'List configured Cookbook servers. Read only.', 'Refresh that same Cookbook server list. Read only.'],
].map(([name, tool, ...prompts]) => ({ name, tool, prompts }))
  .filter(spec => !selected.size || selected.has(spec.name));
if (!cases.length) throw Error('No matching cases selected');

const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = {
  model, status: 'running', cases: [],
  privacy: 'No model names, endpoint details, downloads, server data, tool output, or answer text retained.',
};
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
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': routingMode } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  for (const spec of cases) {
    const result = { name: spec.name, expected_tool: spec.tool, turns: [], cleanup: false, status: 'running' };
    report.cases.push(result); save();
    let session = '';
    try {
      const created = await context.request.post(`${base}/api/session`, { multipart: {
        name: `[cookbook-read-followup] ${spec.name}`, model, endpoint_id: endpointId,
        endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
      }});
      if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
      session = (await created.json()).id;
      page = await context.newPage();
      await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
      const agent = page.locator('#mode-agent-btn');
      if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
      for (let index = 0; index < spec.prompts.length; index++) {
        const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
        await page.locator('textarea#message:visible').fill(spec.prompts[index]);
        await page.locator('textarea#message:visible').press('Enter');
        const response = await waiting;
        const events = parseSSE(await response.text());
        await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 15000 }).catch(() => {});
        const contract = events.find(event => event.type === 'turn_contract') || {};
        const starts = events.filter(event => event.type === 'tool_start');
        const expectedStarts = starts.filter(event => event.tool === spec.tool);
        const outputs = events.filter(event => event.type === 'tool_output' && event.tool === spec.tool);
        const successes = outputs.filter(event => !event.error && (event.exit_code == null || event.exit_code === 0));
        const args = parseArgs(expectedStarts[0]);
        const final = events.filter(event => event.type === 'final_response').map(event => event.content || '').join('')
          || events.filter(event => typeof event.delta === 'string').map(event => event.delta).join('');
        const checks = {
          exact_runtime: contract.routing_experiment === routingMode,
          http_ok: response.ok(),
          clean_route: contract.selection_mode === 'clean_compact_v3_preview',
          cookbook_capability: (contract.active_capabilities || []).includes('cookbook_admin'),
          expected_tool_offered: (contract.offered || []).includes(spec.tool),
          exactly_one_execution: starts.length === 1 && expectedStarts.length === 1,
          empty_arguments: expectedStarts.length === 1 && Object.keys(args).length === 0,
          exactly_one_successful_output: successes.length === 1,
          no_mutation_tool: !starts.some(event => ['download_model', 'serve_model', 'serve_preset', 'stop_served_model', 'cancel_download', 'adopt_served_model'].includes(event.tool)),
          no_stream_error: !events.some(event => ['error', 'invalid_sse'].includes(event.type)),
        };
        result.turns.push({
          index, offered: (contract.offered || []).slice().sort(),
          diagnostic: {
            outputs_failed: outputs.filter(event => event.error || (event.exit_code != null && event.exit_code !== 0)).length,
            failure_categories: outputs.filter(event => event.error || (event.exit_code != null && event.exit_code !== 0)).map(event => {
              const text = String(event.output || '');
              if (/timeout|timed out/i.test(text)) return 'timeout';
              const http = text.match(/HTTP\s+(\d{3})/i);
              if (http) return `http_${http[1]}`;
              if (/incomplete/i.test(text)) return 'partial_inventory';
              return 'other';
            }),
            acknowledges_incomplete_inventory: /incomplete|unavailable|failed|could not|couldn't|unable|cannot verify|timeout|timed out/i.test(final),
            claims_empty_inventory: /no cached models|no models (?:found|cached)|cache is empty/i.test(final),
          },
          tools: starts.map(event => event.tool), argument_keys: Object.keys(args).sort(),
          checks, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed',
        });
        save();
      }
      result.status = result.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
    } catch (error) {
      result.error = String(error).split('\n')[0].slice(0, 400); result.status = 'failed';
    } finally {
      if (page) { await page.close(); page = null; }
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
