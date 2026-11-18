"""Generation-budget presentation: backend values only, absent metadata normal, streaming unharmed."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "static" / "js" / "generationBudget.js"

NODE = r"""
const m = require(process.argv[1]);
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
class El { constructor(t){this.tag=t;this.children=[];this.dataset={};this.attrs={};this.className='';this._text='';}
  setAttribute(k,v){this.attrs[k]=v;} appendChild(c){this.children.push(c);c.parent=this;return c;}
  set textContent(v){this._text=String(v);} get textContent(){return this._text;}
  remove(){ if(this.parent) this.parent.children=this.parent.children.filter(c=>c!==this); }
  querySelectorAll(){ return this.children.filter(c=>c.className.includes('generation-budget-notice')); } }
const doc = { createElement: t => new El(t) };
const host = new El('div'); host.appendChild(Object.assign(new El('div'), {className:'body'}));
const out = {};
for (const [name, report] of Object.entries(input.reports)) {
  out[name] = {describe: m.describeGenerationBudget(report)};
  const el = m.showGenerationBudgetNotice(host, report, doc);
  out[name].shown = !!el; out[name].notices = host.children.filter(c => c.className.includes('generation-budget-notice')).length;
  if (el) out[name].role = el.attrs.role;
}
process.stdout.write(JSON.stringify(out));
"""

CONSTRAINED = {"requested_max_tokens": 4096, "effective_max_tokens": 120, "context_length": 512,
               "estimated_input_tokens": 300, "safety_margin_tokens": 64, "available_output_tokens": 120,
               "safe_to_generate": True, "warning": "Requested 4096 output tokens but only 120 fit in the 512-token context. Choose a larger context or shorten the prompt."}
FITS = {**CONSTRAINED, "requested_max_tokens": 32, "effective_max_tokens": 32, "warning": None}
BLOCKED = {**CONSTRAINED, "effective_max_tokens": 0, "available_output_tokens": -40, "safe_to_generate": False,
           "warning": "The prompt leaves no room for output in the 512-token context."}


def run(reports):
    r = subprocess.run(["node", "-e", NODE, str(MOD)], input=json.dumps({"reports": reports}), text=True, capture_output=True, timeout=15)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_constrained_output_shows_requested_vs_effective_and_how_to_resolve():
    out = run({"c": CONSTRAINED})["c"]
    text = " ".join(out["describe"]["lines"])
    assert out["describe"]["level"] == "reduced" and out["shown"] and out["role"] == "note"
    assert "requested 4096" in text and "effective 120" in text
    assert CONSTRAINED["warning"] in text                       # backend wording is shown verbatim
    assert "larger context" in text and "shorten the prompt" in text


def test_no_space_report_is_blocked_and_says_so():
    out = run({"b": BLOCKED})["b"]
    assert out["describe"]["level"] == "blocked" and "No output space" in out["describe"]["lines"][0]


def test_fitting_missing_and_malformed_reports_show_nothing():
    reports = {"fits": FITS, "absent": None, "empty": {}, "list": [1], "text": {"requested_max_tokens": "4096", "effective_max_tokens": 1},
               "float": {"requested_max_tokens": 1.5, "effective_max_tokens": 1}}
    for name, out in run(reports).items():
        assert out["describe"] is None and out["shown"] is False and out["notices"] == 0, name


def test_latest_report_replaces_the_previous_notice_without_duplicating():
    out = run({"first": CONSTRAINED, "second": BLOCKED, "third": FITS})
    assert out["first"]["notices"] == 1 and out["second"]["notices"] == 1 and out["third"]["notices"] == 0


def test_module_never_recomputes_a_budget():
    source = MOD.read_text(encoding="utf-8")
    assert "fit_output_token_budget" not in source and "Math.min" not in source and "Math.max" not in source
    assert "innerHTML" not in source                              # warning text is never interpreted as HTML


def test_streaming_and_history_hooks_are_metadata_only():
    chat = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
    branch = chat.index("json.type === 'generation_budget'")
    block = chat[branch:chat.index("json.type === 'context_trimmed'", branch)]
    assert "showGenerationBudgetNotice(holder, json)" in block
    assert "accumulated" not in block and "roundText" not in block and "renderDelta" not in block   # never treated as reply text
    assert chat.count("json.type === 'generation_budget'") == 1
    terminal = chat[chat.index("if (_nextIsError || json.status >= 400)"):]
    terminal = terminal[:terminal.index("break;")]
    assert "json.generation_budget" in terminal and "createTerminalStreamError(json)" in terminal   # error text preserved
    renderer = (ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    assert "metadata?.generation_budget" in renderer and "role === 'assistant'" in renderer
