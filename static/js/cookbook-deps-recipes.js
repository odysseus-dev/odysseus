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
  // PXQ checkpoints (PXA, github.com/poisonxa16/pxa). vLLM cannot read a
  // PXQ file, and current upstream vLLM builds do not target the cards PXQ
  // is made for (Tesla P100 / V100). The project publishes a vLLM sidecar
  // image per card family that serves checkpoints converted from a PXQ
  // GGUF. It is a container image only, so the pip variant says so instead
  // of installing anything. The docker variant picks sm60 (P100) or sm70
  // (V100) from nvidia-smi and pins the image to the v2026.10 sidecar tag.
  {
    backend: 'vllm',
    label: 'PXQ model (PXA vLLM sidecar, P100 / V100)',
    match: (m) => /pxq/i.test(m || ''),
    variants: {
      pip:    { run: false, commands: ['echo "The PXA vLLM sidecar ships as a container image, not a pip package. Switch this recipe to Docker."'], venv: false },
      docker: { run: false, commands: ['bash -eu <<\'PXA\'\ncaps=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | tr -d \'[:blank:]\' | sort -u | paste -sd\' \')\ncase "$caps" in\n  6.0) arch=sm60 ;;\n  7.0) arch=sm70 ;;\n  *) echo "The PXA vLLM sidecar has one image per card family, P100 (6.0) or V100 (7.0); this host reports: $caps"; exit 1 ;;\nesac\n# The sidecar images are tagged per feature release (v2026.10), not per patch release.\ndocker pull "ghcr.io/poisonxa16/pxa-vllm:$arch-v2026.10"\necho "The sidecar serves a checkpoint converted from a PXQ GGUF: https://github.com/poisonxa16/pxa/blob/main/docs/VLLM.md"\nPXA'] },
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
    label: 'MLX image models (HiDream)',
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
  // PXQ GGUF files (PXA, github.com/poisonxa16/pxa). Stock llama.cpp cannot
  // load the PXQ tensor types, so a PXQ file needs the PXA engine, a
  // llama.cpp-derived build for Pascal (sm_60/61) and Volta (sm_70) that
  // ships as a release tarball with its CUDA runtime bundled. The pip
  // variant installs no Python package: it checks the tarball's floors
  // (Linux x86_64, glibc 2.35, compute capability 6.0/6.1/7.0) before the
  // 1.3 GB download, picks the tarball that matches the host's glibc (the default build for 2.38+, the ubuntu22.04 build for 2.35+), verifies the release's sha256, and unpacks under
  // ~/.local/share/pxa. The docker variant pins the image to the release tag.
  {
    backend: 'llama_cpp',
    label: 'PXQ GGUF (PXA engine, Pascal / Volta)',
    match: (m) => /pxq/i.test(m || ''),
    variants: {
      pip:    { run: false, commands: ['bash -eu <<\'PXA\'\n[ "$(uname -m)" = x86_64 ] || { echo "PXA ships Linux x86_64 binaries only."; exit 1; }\nglibc=$(getconf GNU_LIBC_VERSION | awk \'{print $2}\')\necho "$glibc" | awk \'{ split($1, v, "."); exit !(v[1] > 2 || (v[1] == 2 && v[2] >= 35)) }\' || { echo "PXA needs glibc 2.35 or newer (Ubuntu 22.04+, Debian 12+, RHEL 9+); this host has $glibc."; exit 1; }\ncaps=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | tr -d \'[:blank:]\' | sort -u | paste -sd\' \')\n[ -n "$caps" ] || { echo "nvidia-smi reported no GPUs."; exit 1; }\nfor c in $caps; do case "$c" in 6.0|6.1|7.0) ;; *) echo "PXA is built for Pascal (6.0, 6.1) and Volta (7.0); this host reports: $caps"; exit 1 ;; esac; done\nurls=$(curl -fsSL https://api.github.com/repos/poisonxa16/pxa/releases/latest | grep \'"browser_download_url"\' | cut -d\'"\' -f4 | grep -E \'/pxa-v[^/]*linux-x86_64[^/]*\\.tar\\.gz$\' || true)\nu22=$(echo "$urls" | grep -- \'-ubuntu22\\.04\\.tar\\.gz$\' | sed -n 1p || true)\nu24=$(echo "$urls" | grep -v -- \'-ubuntu22\\.04\\.tar\\.gz$\' | sed -n 1p || true)\nif echo "$glibc" | awk \'{ split($1, v, "."); exit !(v[1] > 2 || (v[1] == 2 && v[2] >= 38)) }\'; then url=${u24:-$u22}; else url=$u22; fi\n[ -n "$url" ] || { echo "Could not find a tarball for glibc $glibc in the latest PXA release (the default build needs 2.38+, the ubuntu22.04 build 2.35+)."; exit 1; }\nmkdir -p "$HOME/.local/share/pxa" && cd "$HOME/.local/share/pxa"\ntgz=$(basename "$url")\ncurl -fL -o "$tgz" "$url" && curl -fL -o "$tgz.sha256" "$url.sha256"\nsha256sum -c "$tgz.sha256"\ntop=$(tar tzf "$tgz" | sed -n 1p | cut -d/ -f1)\ntar xzf "$tgz" && ln -sfn "$top" current\necho "PXA $top installed. Serve a GGUF with: $HOME/.local/share/pxa/current/run-server.sh -m /path/to/model.gguf -ngl 99 -c 8192"\nPXA'], venv: false },
      docker: { run: false, commands: ['tag=$(curl -fsSL https://api.github.com/repos/poisonxa16/pxa/releases/latest | grep \'"tag_name"\' | cut -d\'"\' -f4) && [ -n "$tag" ] && docker pull "ghcr.io/poisonxa16/pxa:$tag"'] },
    },
  },
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

// Whether the panel should show the venv activate line above a variant's
// commands. True unless the variant sets venv: false (a variant that installs
// no Python package, such as a release tarball or a note).
export function recipeUsesVenv(recipe, variant) {
  if (!recipe) return true;
  const v = (recipe.variants || {})[variant] || (recipe.variants || {}).pip;
  return !(v && v.venv === false);
}

// Whether the panel's Run button can execute a variant. The Cookbook runner
// (/api/model/serve) only accepts one command that starts with an allowlisted
// binary (python, vllm, llama-server, ...) and has no `&&`, `;` or `$(`, so a
// multi-step installer variant sets run: false and is Copy-only.
export function recipeRunnable(recipe, variant) {
  if (!recipe) return true;
  const v = (recipe.variants || {})[variant] || (recipe.variants || {}).pip;
  return !(v && v.run === false);
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
