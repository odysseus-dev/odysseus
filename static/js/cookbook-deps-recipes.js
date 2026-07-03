// Per-backend × per-model install recipes for the Dependencies tab.
//
// Each entry says: when you're about to serve `model` on `backend`, here's
// the exact shell sequence to make the venv + install the right packages.
// Entries are matched first-hit; put the more specific patterns ABOVE the
// generic fallback for that backend.

// Recipes carry two variants per entry:
//   variants.pip    → install into the configured venv via uv/pip
//   variants.docker → pull the official container image
//
// The renderer prepends a `source <venv>/bin/activate` for the pip variant
// (env_prefix handles activation for Run). The docker variant skips the
// activate line — `docker pull` doesn't need a venv.

const _RECIPES = [
  // ── vllm ──────────────────────────────────────────────────────────────
  // MiniMax M2/M2.7 — same as the generic vllm install/image for now;
  // kept as its own entry so future model-specific patches land in one
  // obvious place without touching the catch-all.
  {
    backend: 'vllm',
    label: 'MiniMax M2 / M2.7',
    match: (m) => /minimax[-_]?m\s?2(\.7)?/i.test(m || ''),
    variants: {
      pip:    { commands: ['uv pip install -U vllm --torch-backend auto'] },
      docker: { commands: ['docker pull vllm/vllm-openai:latest'] },
    },
  },
  // Generic vllm fallback.
  {
    backend: 'vllm',
    label: 'Any vLLM model',
    match: () => true,
    variants: {
      pip:    { commands: ['uv pip install -U vllm --torch-backend auto'] },
      docker: { commands: ['docker pull vllm/vllm-openai:latest'] },
    },
  },

  // ── sglang ────────────────────────────────────────────────────────────
  {
    backend: 'sglang',
    label: 'Any SGLang model',
    match: () => true,
    variants: {
      pip:    { commands: ['uv pip install -U "sglang[all]" --torch-backend auto'] },
      docker: { commands: ['docker pull lmsysorg/sglang:latest'] },
    },
  },

  // ── llama.cpp ─────────────────────────────────────────────────────────
  {
    backend: 'llama_cpp',
    label: 'Any GGUF model',
    match: () => true,
    variants: {
      pip:    { commands: ['CMAKE_ARGS="-DGGML_CUDA=on" uv pip install -U "llama-cpp-python[server]"'] },
      docker: { commands: ['docker pull ghcr.io/ggml-org/llama.cpp:server-cuda'] },
    },
  },
];

export const RECIPE_VARIANTS = ['pip', 'docker'];
export const RECIPE_DEFAULT_VARIANT = 'pip';

// Get the commands array for a recipe + variant. Falls back to pip when
// the requested variant isn't defined for the recipe.
export function recipeCommands(recipe, variant) {
  if (!recipe) return [];
  const v = (recipe.variants || {})[variant] || (recipe.variants || {}).pip;
  return (v && v.commands) || [];
}

// Backends we surface a recipe panel for. Other rows in the Dependencies
// list keep the existing flat Install/Reinstall button without an expand
// affordance.
export const RECIPE_BACKENDS = new Set(['vllm', 'sglang', 'llama_cpp']);

// All recipe entries for a given backend, in catalog order. The first one
// is the model-specific match (when present); the last is always the
// generic fallback.
export function recipesForBackend(backend) {
  return _RECIPES.filter((r) => r.backend === backend);
}

// Pick the best recipe for a backend + model id. Returns the catalog
// fallback when nothing more specific matches, or null if the backend
// isn't in the catalog at all.
export function pickRecipe(backend, modelId) {
  const candidates = recipesForBackend(backend);
  if (!candidates.length) return null;
  for (const r of candidates) {
    try { if (r.match(modelId)) return r; } catch (_) {}
  }
  return candidates[candidates.length - 1] || null;
}
