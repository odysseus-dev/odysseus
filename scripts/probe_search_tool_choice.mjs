#!/usr/bin/env node
// Read-only endpoint probe: emitted tool calls are recorded, never executed.
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
const tools = JSON.parse(execFileSync((process.env.PYTHON || "python3"), ['-c',
  'import json; from src.clean_agent_preview import compact_schemas; from src.tool_schemas import FUNCTION_TOOL_SCHEMAS; print(json.dumps(compact_schemas([s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "web_search"])))'], {encoding:'utf8'}));
const model = process.env.MODEL || 'model-f';
const endpoint = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const prompts = process.env.PROBE_PROMPTS ? JSON.parse(process.env.PROBE_PROMPTS) : ['Catch me up on the biggest AI developments this week. Explain why they matter and link your sources.', 'serch latest ai news pls'];
const results = [];
let system = 'You are Odysseus. Use web_search to find current information relevant to the user request.';
if (process.env.CANONICAL_SYSTEM === '1') {
  system = execFileSync((process.env.PYTHON || "python3"), ['-c', `
import ast, sys
from datetime import datetime, timezone
from src.clean_agent_preview import native_input_files_clause
tree = ast.parse(sys.stdin.read())
fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'stream_preview')
assignment = next(n for n in fn.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'system' for t in n.targets))
runtime_scope_clause = 'This is a tool preview connected to the authenticated user’s real data. '
native_workspace_enabled = False
client_runtime_context = None
shell_clause = 'Shell commands are disabled. '
print(eval(compile(ast.Expression(assignment.value), '<canonical-system-expression>', 'eval')))
`], {input:fs.readFileSync('src/clean_agent_preview.py','utf8'),encoding:'utf8'}).trim();
}
system += process.env.SYSTEM_APPEND || '';
for (const prompt of prompts) {
  for (const choice of (process.env.PROBE_CHOICES ? JSON.parse(process.env.PROBE_CHOICES) : ['auto', 'required', {type:'function', function:{name:'web_search'}}])) {
    const started = performance.now();
    const response = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, signal:AbortSignal.timeout(90000),
      body:JSON.stringify({model, messages:[{role:'system',content:system},{role:'user',content:prompt}], ...(process.env.OMIT_TOOLS === '1' ? {} : {tools, tool_choice:choice}), temperature:0, max_tokens:256, stream:false, chat_template_kwargs:{enable_thinking:false}})});
    const data = await response.json();
    const result = {prompt,choice,status:response.status,seconds:(performance.now()-started)/1000,message:data.choices?.[0]?.message,error:data.error};
    results.push(result); console.log(JSON.stringify(result));
  }
}
const target = `reports/search-tool-choice-probe-${Date.now()}.json`;
fs.writeFileSync(target, JSON.stringify({model,system,tools_omitted:process.env.OMIT_TOOLS === '1',tools,results},null,2)+'\n');
console.log(target);
