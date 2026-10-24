// ============================================
// VISION MODEL DETECTION
// ============================================
// Which models take an image as input, and therefore must not be launched with
// a text-only server. `mlx_lm.server` accepts no images: a vision checkpoint
// sent there comes up looking healthy while there is no way to hand it a
// picture, which is the bug this module closes.
//
// Rows that come from the hwfit catalogs or from the cached-model scan already
// carry a `modality` field decided in Python (services/hwfit/vision.py) — that
// is the single source of truth and it wins here whenever it is present. The
// marker list below is only for model names typed into the serve form by hand,
// which reach this code without passing through either Python path. The two
// lists are pinned together by tests/test_cookbook_vision_marker_parity.py.
//
// Dependency-free on purpose, so node can import it directly in tests the way
// cookbookPorts.js is tested — cookbook.js itself pulls in browser globals and
// cannot run under node.

export const VISION_PIPELINE_TAG = 'image-text-to-text';

// Keep in sync with VISION_NAME_MARKERS in services/hwfit/vision.py.
export const VISION_NAME_MARKERS = [
  'vl',
  'vlm',
  'vision',
  'llava',
  'moondream',
  'internvl',
  'idefics',
  'pixtral',
  'smolvlm',
  'paligemma',
  'minicpm-v',
];

// Whole delimited tokens only, so "navlm" and "revlon" do not match on "vl".
const _MARKER_RE = new RegExp(`(?:^|[-_./])(${VISION_NAME_MARKERS.join('|')})(?:$|[-_./0-9])`, 'i');

// Published with the vision tower removed — a text model despite the name.
const _SKIP_RE = /skip[-_.]vision/i;

// Every field a model row identifies ITSELF by. `path` is deliberately
// excluded: it is the containing directory, not the model — the HF-cache scan
// sets it to the cache root and the local-directory scan to the parent of the
// model folder, whose own name is repo_id. Matching the marker there would
// read how someone named a directory as if it were model metadata: a folder
// called "vision-models" would flag every text model inside it, and one called
// "skip-vision-archive" would send a genuine VLM back to the text-only server.
function _names(model) {
  if (!model || typeof model !== 'object') return '';
  return [model.name, model.repo_id, model.id]
    .filter(v => v != null && v !== '')
    .map(String)
    .join(' ');
}

export function isVisionModel(model) {
  if (!model || typeof model !== 'object') return false;
  const names = _names(model);
  if (_SKIP_RE.test(names)) return false;
  const modality = String(model.modality || '').trim().toLowerCase();
  if (modality) return modality === 'vision';
  const tag = String(model.pipeline_tag || '').trim().toLowerCase();
  if (tag === VISION_PIPELINE_TAG) return true;
  return _MARKER_RE.test(names);
}
