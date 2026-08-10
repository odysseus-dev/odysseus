// static/js/cookbookMlxEngines.js
//
// MLX serving engines. Every one speaks the OpenAI-compatible /v1 protocol, so
// only the launch command differs — Odysseus talks to all of them as `openai`.
// Add a new engine by adding a row here (+ its binary to _SERVE_CMD_ALLOWLIST
// in routes/cookbook_helpers.py).
//
// Pure (fields in, command string out) so the launch commands are unit-testable
// under node; cookbook.js itself pulls in browser-only modules and can't load.
// Same split as cookbookPorts.js / cookbookProgressSignal.js.

// Local copy of cookbook.js's _shellQuote — importing it would drag the whole
// browser-bound module back in and defeat the point of this file.
function _q(value) {
  return "'" + String(value ?? '').replace(/'/g, "'\\''") + "'";
}

// Quote a directory argument, keeping a leading `~` expandable: single quotes
// would make the shell take the tilde literally, so emit it as "$HOME" with the
// rest quoted next to it ("$HOME"'/models' is one word to the shell).
function _pathArg(path) {
  const s = String(path || '');
  if (s === '~') return '"$HOME"';
  if (s.startsWith('~/')) return '"$HOME"' + _q(s.slice(1));
  return _q(s);
}

// A serve aimed at a REMOTE host has to bind 0.0.0.0 or the Odysseus host can't
// reach it; a local serve stays on loopback. Both MLX engines take --host.
function _bindHost(f) {
  return (f && f.host) ? '0.0.0.0' : '127.0.0.1';
}

// oMLX serves a DIRECTORY of models and the client picks one by name via the
// request `model` field, so --model-dir is the PARENT of the model's own
// folder. Derive it from the same resolved path mlx-lm's --model receives — NOT
// from the download base dir, which for HF-cache layouts holds
// `models--org--name/snapshots` indirections rather than loadable bundles.
//
// A bare repo id ("mlx-community/Qwen3-4B-4bit") is not a filesystem path at
// all: mlx-lm resolves it through the HF cache, oMLX can't. That returns '' and
// the command below simply leaves --model-dir off, so the user fills the model
// path in (or edits the command) instead of being handed a guessed directory.
export function mlxModelDirFor(serveModel) {
  const p = String(serveModel || '').trim().replace(/\/+$/, '');
  if (!/^(?:\/|~\/|\.{1,2}\/)/.test(p)) return '';
  const cut = p.lastIndexOf('/');
  if (cut < 0) return '';
  return p.slice(0, cut) || '/';
}

export const MLX_ENGINES = {
  mlx_lm: {
    label: 'MLX (mlx-lm)',
    // Apple's official single-model server. --model takes the model dir/repo
    // (modelName is already resolved to the local path for cached models).
    cmd: (f, modelName, py3Bin) => {
      let c = `${py3Bin} -m mlx_lm.server --model ${_q(modelName)} --host ${_bindHost(f)} --port ${f.port || '8080'}`;
      const maxTokens = String(f.ctx || '').trim();
      if (/minimax|mini-max/i.test(modelName)) {
        c += ` --temp 0.7 --top-p 0.9 --max-tokens ${maxTokens || '2048'}`;
      } else if (/^\d+$/.test(maxTokens)) {
        // MLX-LM server has no vLLM-style --context-length flag. The closest
        // server-side request budget it exposes is --max-tokens, so wire the
        // Cookbook Context/Auto control there for MLX launches.
        c += ` --max-tokens ${maxTokens}`;
      }
      return c;
    },
  },
  omlx: {
    label: 'oMLX',
    // Serves a whole model DIRECTORY (auto-discovers subdirs); the client picks
    // the model via the request `model` field. FastAPI/uvicorn under the hood,
    // so --host is the standard bind flag, same as the mlx-lm row.
    cmd: (f) => {
      const dir = (f._mlx_model_dir || '').toString().trim();
      let c = 'omlx serve';
      // No resolvable directory → omit the flag rather than point oMLX at a
      // guessed "$HOME/models" that probably holds nothing.
      if (dir) c += ` --model-dir ${_pathArg(dir)}`;
      c += ` --host ${_bindHost(f)} --port ${f.port || '8000'}`;
      if (f.max_seqs && f.max_seqs.toString().trim()) c += ` --max-concurrent-requests ${f.max_seqs.toString().trim()}`;
      return c;
    },
  },
};

export function buildMlxServeCmd(f, modelName, py3Bin) {
  return (MLX_ENGINES[f && f.mlx_engine] || MLX_ENGINES.mlx_lm).cmd(f, modelName, py3Bin);
}
