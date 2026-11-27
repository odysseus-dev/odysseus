// Per-backend × per-model install recipes for the Dependencies tab.
//
// Each entry says: when you're about to serve `model` on `backend`, here's
// the exact shell sequence to make the venv + install the right packages.
// Entries are matched first-hit; put the more specific patterns ABOVE the
// generic fallback for that backend.

// Recipes carry two variants per entry:
//   variants.pip    → install into the configured venv via pip/uv
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

  // ── MLX ───────────────────────────────────────────────────────────────
  {
    backend: 'mlx_lm',
    label: 'Any MLX model',
    match: () => true,
    variants: {
      pip:    { commands: ['python -m pip install -U mlx-lm'] },
    },
  },
  {
    backend: 'mflux',
    label: 'mflux-compatible MLX image models',
    match: () => true,
    variants: {
      pip:    { commands: ['python -m pip install -U mflux fastapi uvicorn python-multipart'] },
    },
  },
  {
    backend: 'boogu_image_mlx',
    label: 'MLX image models (Boogu)',
    match: () => true,
    variants: {
      pip:    { commands: ['python -m pip install -U git+https://github.com/xocialize/boogu-image-mlx.git fastapi uvicorn python-multipart pillow'] },
    },
  },
  {
    backend: 'mlx_vlm',
    // Two uses now: serving vision models (image input) and backing HiDream.
    label: 'MLX vision models and HiDream images',
    match: () => true,
    variants: {
      pip:    { commands: ['python -m pip install -U fastapi uvicorn python-multipart mlx mlx-vlm "transformers>=4.57.0,<6.0" huggingface_hub safetensors numpy pillow tqdm sentencepiece hf_transfer'] },
    },
  },
  {
    backend: 'mlx_lama_swift',
    label: 'MLX image editing (LaMa / MI-GAN)',
    match: () => true,
    variants: {
      pip: {
        commands: [
          'python -m pip install -U fastapi uvicorn python-multipart pillow huggingface_hub',
          'BRIDGE_DIR="${ODYSSEUS_ROOT:-$PWD}/swift/odysseus-mlx-image-bridge"; test -d "$BRIDGE_DIR" || { echo "Run this from an Odysseus checkout that includes swift/odysseus-mlx-image-bridge, or set ODYSSEUS_ROOT=/path/to/odysseus."; exit 1; }',
          'BRIDGE_DIR="${ODYSSEUS_ROOT:-$PWD}/swift/odysseus-mlx-image-bridge"; cd "$BRIDGE_DIR" && swift build -c release --product odysseus-mlx-inpaint',
          'BRIDGE_DIR="${ODYSSEUS_ROOT:-$PWD}/swift/odysseus-mlx-image-bridge"; mkdir -p "$HOME/.local/bin" && cp "$BRIDGE_DIR/.build/release/odysseus-mlx-inpaint" "$HOME/.local/bin/odysseus-mlx-inpaint"',
          'MLX_METALLIB="$(python - <<\'PY\'\nimport pathlib, sys\ntry:\n    import mlx\nexcept Exception as exc:\n    raise SystemExit(f"mlx Python package is required for mlx.metallib: {exc}")\nroot = pathlib.Path(mlx.__file__).resolve().parent\nfor name in ("lib/mlx.metallib", "mlx.metallib", "lib/default.metallib", "default.metallib"):\n    path = root / name\n    if path.exists():\n        print(path)\n        break\nelse:\n    raise SystemExit(f"No MLX metallib found under {root}")\nPY\n)"; mkdir -p "$HOME/.local/bin" && cp "$MLX_METALLIB" "$HOME/.local/bin/mlx.metallib" && cp "$MLX_METALLIB" "$HOME/.local/bin/default.metallib"',
        ],
      },
    },
  },
  {
    backend: 'mlx_ddcolor_swift',
    label: 'MLX image editing (DDColor)',
    match: () => true,
    variants: {
      pip: {
        commands: [
          'python -m pip install -U fastapi uvicorn python-multipart pillow huggingface_hub',
          'BRIDGE_DIR="${ODYSSEUS_ROOT:-$PWD}/swift/odysseus-mlx-image-bridge"; test -d "$BRIDGE_DIR" || { echo "Run this from an Odysseus checkout that includes swift/odysseus-mlx-image-bridge, or set ODYSSEUS_ROOT=/path/to/odysseus."; exit 1; }',
          'BRIDGE_DIR="${ODYSSEUS_ROOT:-$PWD}/swift/odysseus-mlx-image-bridge"; cd "$BRIDGE_DIR" && swift build -c release --product odysseus-mlx-colorize',
          'BRIDGE_DIR="${ODYSSEUS_ROOT:-$PWD}/swift/odysseus-mlx-image-bridge"; mkdir -p "$HOME/.local/bin" && cp "$BRIDGE_DIR/.build/release/odysseus-mlx-colorize" "$HOME/.local/bin/odysseus-mlx-colorize"',
          'MLX_METALLIB="$(python - <<\'PY\'\nimport pathlib, sys\ntry:\n    import mlx\nexcept Exception as exc:\n    raise SystemExit(f"mlx Python package is required for mlx.metallib: {exc}")\nroot = pathlib.Path(mlx.__file__).resolve().parent\nfor name in ("lib/mlx.metallib", "mlx.metallib", "lib/default.metallib", "default.metallib"):\n    path = root / name\n    if path.exists():\n        print(path)\n        break\nelse:\n    raise SystemExit(f"No MLX metallib found under {root}")\nPY\n)"; mkdir -p "$HOME/.local/bin" && cp "$MLX_METALLIB" "$HOME/.local/bin/mlx.metallib" && cp "$MLX_METALLIB" "$HOME/.local/bin/default.metallib"',
        ],
      },
    },
  },

  // ── Diffusers ────────────────────────────────────────────────────────
  {
    backend: 'diffusers',
    label: 'Any Diffusers image model',
    match: () => true,
    variants: {
      pip:    { commands: ['python -m pip install -U "diffusers[torch]" torchvision accelerate scipy python-multipart'] },
    },
  },
  {
    backend: 'krea_diffusers',
    label: 'Latest Diffusers from Git',
    match: () => true,
    variants: {
      pip:    { commands: ['python -m pip install -U git+https://github.com/huggingface/diffusers.git torchvision accelerate scipy python-multipart'] },
    },
  },
  {
    backend: 'sam_mask',
    label: 'SAM object mask tools',
    match: () => true,
    variants: {
      pip:    { commands: ['python -m pip install -U torch torchvision transformers accelerate pillow'] },
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
export const RECIPE_BACKENDS = new Set(['vllm', 'sglang', 'mlx_lm', 'mflux', 'boogu_image_mlx', 'mlx_vlm', 'mlx_lama_swift', 'mlx_ddcolor_swift', 'diffusers', 'krea_diffusers', 'sam_mask', 'llama_cpp']);

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
