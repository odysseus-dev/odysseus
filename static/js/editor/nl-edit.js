// Natural-language editor driver. Turns a user prompt into a list of
// editor-control ops (via a server planner / LLM when available, otherwise a
// deterministic built-in "basic mode" planner), executes them one at a time
// through the editor control API, captures a screenshot after each step and
// finally collapses the whole batch into a single undo step.
//
// Region-aware: prompts that touch the subject/sky/background are mapped to
// SAM / color-range selections plus masked filters and adjustments, so the
// edit is confined to the intended region instead of the whole layer.

const DEFAULT_PLAN_URL = '/api/editor/plan';
const DEFAULT_CHAT_URL = '/api/chat';
const DEFAULT_SESSION_URL = '/api/default-chat';

// Compact op catalog handed to the model so it can plan with real ops.
export const OP_CATALOG = [
  'document.info', 'layers.list', 'layers.select {id|index|name}', 'layers.addRaster',
  'layers.duplicate', 'layers.delete', 'layers.setOpacity {opacity 0-1}',
  'layers.setBlend {blendMode}', 'layers.group', 'layers.ungroup',
  'layers.placeImage {dataUrl,masked,blend,opacity,x,y,w,h}', 'layers.addMaskFromSelection {invert}',
  'layers.removeMask', 'layers.applyMask', 'mask.invert', 'mask.feather {radius}',
  'layers.getImage {id|index|name}', 'layers.getBounds {id|index|name}',
  'ai.removeBackground', 'ai.generativeFill {prompt}', 'ai.removeObject {prompt}',
  'ai.harmonize {strength}', 'ai.generativeUpscale {scale}', 'ai.generateImage {prompt,width,height}',
  'ai.generativeExpand {width,height,anchor,prompt}',
  'selection.selectAll', 'selection.deselect', 'selection.invert', 'selection.rectangle {x,y,w,h}',
  'selection.ellipse {x,y,w,h}', 'selection.subject {label}', 'selection.face', 'selection.sky', 'selection.background', 'selection.samText {text}',
  'selection.colorRange {color,fuzziness}', 'selection.hasMask',
  'adjustments.apply {type,params}', 'adjustments.applyMasked {brightness,contrast,saturation,warmth,invert}',
  'photo.autoTone', 'retouch.skin {smooth,opacity,blemish}', 'retouch.blemish {radius,threshold}', 'retouch.teeth {desat,bright}',
  'filters.apply {id}', 'filters.applyMasked {id,radius,amount,invert}',
  'transform.start', 'transform.apply', 'transform.cancel', 'text.add {text,x,y,color,size}', 'text.layout {anchor,sizePct,marginPct}',
  'layers.rename {name}', 'layers.setVisibility {visible}', 'layers.reorder {direction:up|down|top|bottom}',
  'layers.rasterize', 'layers.mergeDown', 'layers.flatten', 'layers.align {mode:left|center|right|top|middle|bottom|distribute-h|distribute-v}',
  'layers.addStyle {type,params}',
  'selection.polygon {points}', 'selection.fill {color}', 'fill.flood {x,y,color,tolerance}', 'selection.delete', 'selection.copy {cut}',
  'selection.paste', 'selection.feather {radius}', 'selection.expand {amount}', 'selection.contract {amount}',
  'text.edit {content,font,size,color,align,tracking,lineHeight}',
  'layer.flip {axis:h|v,id|index|name}', 'layer.rotate {degrees:90|180|270,id|index|name}',
  'document.rotate {degrees:90|180|270}', 'document.flip {axis:h|v}',
  'document.crop {x,y,width,height}', 'document.extend {width,height,anchor}', 'document.savePsd', 'document.exportLayers',
  'document.list', 'document.duplicate {name}', 'document.create {width,height,name}', 'document.switch {id}', 'document.openPsd {dataUrl,name}',
  'run_editor_script {steps}',
  'export.png', 'export.gallery',
];

export const SYSTEM_INSTRUCTION = [  'You are a photo-editing planner that controls an image editor through a fixed',
  'operation API. Convert the user request into a JSON object',
  '{"reply": string, "steps": [{"op": string, "params": object}]}.',
  'Use ONLY these ops:',
  OP_CATALOG.join('; '),
  'You are given the current document state and a screenshot of the image.',
  'Rules:',
  '1) To edit only part of the image, first create a region selection',
  '(selection.subject / selection.sky / selection.samText / selection.colorRange),',
  'then apply the change with adjustments.applyMasked or filters.applyMasked.',
  '2) adjustments.applyMasked {brightness,contrast,saturation,warmth,invert}: brightness,',
  'contrast and warmth are pixel amounts -100..100 (0 = neutral); saturation is a',
  'multiplier (1 = neutral, 1.4 = +40%). filters.applyMasked {id,radius,amount,invert}:',
  'invert:true edits everything EXCEPT the selection (e.g. blur the background).',
  '3) adjustments.apply {type,params} adds an adjustment layer. For brightness-contrast',
  'pass brightness/contrast as -100..100 (0 neutral). For vibrance pass vibrance as',
  '-100..100. For hue-saturation pass hue in degrees (-180..180) and saturation as a',
  'multiplier (1 neutral). Valid types: brightness-contrast, vibrance, hue-saturation,',
  'black-white, photo-filter, color-balance, levels, curves, exposure, shadows-highlights.',
  '4) filters.apply {id} with id one of: blur-gaussian, sharpen, unsharp-mask.',
  '5) Prefer ONE decisive step over many tiny ones. Use absolute, verified magnitudes',
  '(no placeholders). Never invent op names or params outside this catalog.',
  '6) You MUST emit the matching selection.* step BEFORE any adjustments.applyMasked',
  'or filters.applyMasked in the SAME plan: face -> selection.face; sky -> selection.sky;',
  '"blur the background behind the subject/person" -> selection.subject then',
  'filters.applyMasked {id:"blur-gaussian",invert:true}. Never emit a masked op with no',
  'preceding selection.',
  '7) NEVER use the specialty effect types (invert, gradient-map, color-lookup, posterize,',
  'threshold) unless the user explicitly names that effect; they cause false-colour or',
  'banded output. For any lighting/tone fix use exposure, shadows-highlights or',
  'brightness-contrast with modest amounts (each <=35).',
  '8) For a format/platform request ("instagram ready", "square", "story", "post", "1:1",',
  '"1080x1350", "profile"), you MUST emit document.crop with x/y/width/height computed for',
  'the target aspect ratio (square = the largest centred 1:1 crop of the current document),',
  'optionally followed by a light vibrance. Do not merely describe the crop.',
  '9) Avoid imperceptible edits: for a generic request ("make it pop", "clean this up",',
  '"edit this photo", "make it pretty", "fix the skin") make a clearly visible but natural',
  'change (e.g. vibrance 20..35 plus contrast 8..18 plus a small warmth), not a token amount.',
  '10) For a GENERIC intent ("edit this photo", "make it pretty", "make it pop", "colors nicer",',
  '"clean this up", "make it look professional", "fix the lighting", "it\'s too dark") your FIRST',
  'step MUST be photo.autoTone (Camera-Raw-style auto level/white-balance/exposure/contrast),',
  'optionally followed by one visible stylistic step. Never return an empty or single token step.',
  '11) "fix the skin"/"smooth skin"/"look better"/"retouch": first selection.face (or',
  'selection.subject), then retouch.skin {smooth:24,opacity:0.85,blemish:true}; this uses',
  'frequency separation (keeps pores) intersected with skin tones. Do NOT use a global blur.',
  '12) "fix my teeth"/"whiten teeth": selection.samText {text:"teeth"} then retouch.teeth',
  '{desat:0.6,bright:16}. If sampling is unavailable fall back to selection.face + retouch.teeth.',
  'Style guide (map the intent to these edit characteristics; never over-process):',
  '- "fix the lighting"/"it\'s too dark": raise exposure/shadows modestly (exposure 10..25',
  'or shadows 20..35) with a small contrast lift; if the image is dark do NOT stack several',
  'global lifts — one or two steps, and never a specialty effect type.',
  '- "dramatic sky": LOWER the sky exposure slightly (-10..-25), RAISE contrast/clarity',
  '(contrast 25..45), deepen blue (hue-saturation or photo-filter blue), and PROTECT',
  'highlights/blown clouds. Do NOT brighten and do NOT push saturation above +25%.',
  '- "cinematic": teal/orange split-tone (color-balance warm highlights / cool shadows),',
  'lifted blacks, mild contrast; keep skin neutral.',
  '- "vivid": +vibrance 20..40 only; avoid saturation clipping.',
  '- "warm"/"cool": white-balance/photo-filter shift of +/-15..30, keep exposure.',
  '- "black and white": black-white + a little contrast (15..35).',
  '- "sharper": unsharp-mask 40..80; do not combine with strong saturation.',
  '- "brighten/remove the face": select the face first (selection.face) then a',
  'masked brightness lift that is VISIBLE but natural (brightness 18..28, optional',
  'contrast 4..8). The face must read clearly brighter while skin stays natural; do',
  'not use a token amount and never a global exposure change.',
  '- "fix the skin"/"retouch"/"make me look better": selection.face (or selection.subject)',
  'then retouch.skin {smooth:24,opacity:0.85,blemish:true}; keep an eye on the background (do',
  'NOT blur anything outside the skin mask; the client masks it, but choose the face region).',
  '- "fix my teeth"/"whiten teeth": selection.samText {text:"teeth"} then retouch.teeth.',
  '- "clean this up"/"edit this photo"/"make it pretty"/"make it pop"/generic: photo.autoTone',
  'first (auto level + white balance + exposure + contrast), then optionally vibrance 15..30',
  'and contrast 6..12. The result must be a clearly visible, natural improvement.',
  'Method choice (agent): prefer the deterministic ops above whenever they can',
  'achieve the intent; use the AI image-edit tool (content-aware remove/replace an',
  'object, background removal, upscale, denoise, face restore) only when the change',
  'needs synthesised pixels; use image generation only to ADD content that does not',
  'exist in the photo (e.g. "a full moon"). When using an AI edit or generation,',
  'create the region selection first (selection.subject/sky/samText) so the result is',
  'masked to that region, and the result is placed as a NEW layer on top of the',
  'original (never overwriting it), inside the same undo group.',
  'Return JSON only, with no prose before or after.',
].join(' ');

// Extra guidance appended for self-check calls so the live route returns a FULL
// corrected plan (the client reverts the previous attempt before running it).
export const CHECK_INSTRUCTION = [
  'This is a self-check. Judge whether the current result matches the user',
  'intent and is NOT over-processed (no blown highlights, no over-saturated',
  'colour, not brighter when it should be darker). If it matches, reply',
  '{"done": true, "reply": string, "steps": []}. Otherwise reply with a FULL',
  'corrected plan {"reply": string, "steps": [...]} that achieves the intent',
  'from the ORIGINAL image with corrected parameters; the previous attempt will',
  'be REVERTED before your plan runs, so do not send a delta of extra steps and',
  'do not describe a change. Prefer conservative amounts on the retry.',
  'For a FACE-BRIGHTEN request, compare the FACE region specifically (use the',
  'faceLuminance before/after numbers when provided): the face must be clearly',
  'brighter. If the change is subtle or the face looks unchanged, return a corrected',
  'plan with selection.face then adjustments.applyMasked {brightness: 22..30}.',
].join(' ');

// Shared intent->ops style guide (single source of truth), served by the server
// at GET /api/editor/style-guide. When present the browser fallback planner uses
// the server's guidance text + intent routes so the client and server rules can
// never drift. Falls back to the built-in SYSTEM_INSTRUCTION when unavailable.
let _STYLE_GUIDE = null;
export function setStyleGuide(guide) {
  _STYLE_GUIDE = guide && typeof guide === 'object' ? guide : null;
  return _STYLE_GUIDE;
}
export function getStyleGuide() { return _STYLE_GUIDE; }
export function activeGuidance() {
  return (_STYLE_GUIDE && typeof _STYLE_GUIDE.guidance === 'string' && _STYLE_GUIDE.guidance)
    ? _STYLE_GUIDE.guidance
    : SYSTEM_INSTRUCTION;
}
export async function fetchStyleGuide(url = '/api/editor/style-guide', fetchImpl = null) {
  const doFetch = fetchImpl || (typeof fetch !== 'undefined' ? fetch : null);
  if (!doFetch) return null;
  try {
    const res = await doFetch(url, { headers: { Accept: 'application/json' } });
    if (!res || !res.ok) return null;
    const data = await res.json();
    return setStyleGuide(data);
  } catch (_) { return null; }
}
function _matchGuideIntent(prompt) {
  if (!_STYLE_GUIDE || !Array.isArray(_STYLE_GUIDE.intents)) return null;
  const p = String(prompt || '').toLowerCase();
  for (const intent of _STYLE_GUIDE.intents) {
    const kws = Array.isArray(intent.keywords) ? intent.keywords : [];
    for (const kw of kws) {
      let hit = false;
      try { hit = kw instanceof RegExp ? kw.test(p) : (kw && p.includes(String(kw).toLowerCase())); } catch (_) { hit = false; }
      if (hit) return { reply: intent.reply || '', steps: JSON.parse(JSON.stringify(intent.steps || [])), planner: 'local', matched: intent.id };
    }
  }
  return null;
}

export function buildPlannerMessage(prompt, state = {}, extra = {}) {
  const lines = [`Document: ${JSON.stringify(state)}`];
  if (extra.mode === 'check') {
    lines.push('This is a self-check step. You are shown the BEFORE image and the AFTER image of the last attempt, plus the original intent. Judge whether the current result matches the intent and is NOT over-processed (no blown highlights, no clipped/over-saturated colour, no brighter-when-it-should-be-darker mistake). If it matches, reply {"reply": string, "done": true, "steps": []}. If it does not match, reply with a FULL corrected plan {"reply": string, "steps": [...]} that achieves the intent from the original image, with corrected parameters (we will REVERT the previous attempt and apply your corrected plan, so do not describe a delta). Prefer lower/more conservative amounts on the retry.');
    if (Array.isArray(extra.applied) && extra.applied.length) {
      lines.push(`Previous attempt (will be reverted and replaced by your corrected plan): ${JSON.stringify(extra.applied)}`);
    }
    if (extra.faceLuminance && (extra.faceLuminance.before != null || extra.faceLuminance.after != null)) {
      lines.push(`Face-region mean luminance (0-255): before=${extra.faceLuminance.before}, after=${extra.faceLuminance.after}. A face-brighten is only done when the after value is clearly higher (roughly +8 or more); otherwise return a corrected plan with a larger masked brightness.`);
    }
  }
  lines.push(`User request: ${prompt}`);
  return lines.join('\n\n');
}

export function isGenericIntent(prompt = '') {
  const p = String(prompt || '').toLowerCase().trim();
  if (!p) return false;
  return /^(edit this|edit the|make it (pretty|pop|look |nice|better|good|professional|amazing)|make the (colou?rs?|tones?|image|photo|picture) (nicer|better|pop|warmer|cooler|more vivid|more dramatic)|colou?rs? nicer|clean (this|it) up|fix (this|it|the (lighting|colors|colours|photo|picture))|improve (this|it|the)|enhance (this|it|the|photo)|touch (this|it) up|it'?s too (dark|bright|flat|dull)|(a ?bit )?(darker|brighter|more vivid|prettier))/.test(p);
}

// Named-colour resolver for fill/flood prompts.
const NAMED_COLORS = {
  red: '#ff0000', blue: '#0000ff', green: '#00a000', yellow: '#ffff00',
  orange: '#ff8000', purple: '#8000ff', pink: '#ff69b4', black: '#000000',
  white: '#ffffff', grey: '#808080', gray: '#808080', cyan: '#00ffff',
  magenta: '#ff00ff', brown: '#8b4513', teal: '#008080',
};
export function colorForName(name = '') {
  const n = String(name).toLowerCase().trim();
  if (/^#[0-9a-f]{3,6}$/.test(n)) return n;
  return NAMED_COLORS[n] || null;
}

export function localPlan(prompt, state = {}) {
  const guided = _matchGuideIntent(prompt);
  if (guided && guided.steps.length) return guided;
  const p = String(prompt || '').toLowerCase();
  const steps = [];
  const first = (arr) => arr[0];
  const wantsSubject = /\bsubject\b|\bperson\b|\bpeople\b|\bportrait\b|\bbehind\b|\bforeground\b/.test(p);
  const wantsSky = /\bsky\b/.test(p);
  const wantsBackground = /\bbackground\b|\bbg\b/.test(p);

  // Region-first: build a selection when the prompt references a region.
  // `hasRegion` gates the masked ops so they only run when a selection exists
  // (an adjustment-layer step does not count as a region).
  let hasRegion = false;
  if (wantsBackground || /\bbehind\b/.test(p)) {
    steps.push({ op: 'selection.background', params: {} });
    hasRegion = true;
  } else if (wantsSky) {
    steps.push({ op: 'selection.sky', params: {} });
    hasRegion = true;
  } else if (wantsSubject) {
    steps.push({ op: 'selection.subject', params: { label: 'person' } });
    hasRegion = true;
  }

  if (/\bblack\s*(and|&|n)?\s*white\b|\bgrayscale\b|\bgreyscale\b|\bmonochrome\b/.test(p)) {
    if (hasRegion) steps.push({ op: 'adjustments.applyMasked', params: { saturation: 0 } });
    else steps.push({ op: 'adjustments.apply', params: { type: 'black-white', params: {} } });
  }
  if (/\bwarm(er)?\b|\bgolden\b|\bsunset\b/.test(p)) {
    if (hasRegion) steps.push({ op: 'adjustments.applyMasked', params: { warmth: 22, brightness: 8 } });
    else steps.push({ op: 'adjustments.apply', params: { type: 'white-balance', params: { temperature: 22 } } });
  }
  if (/\bcool(er)?\b|\bcold\b/.test(p)) {
    if (hasRegion) steps.push({ op: 'adjustments.applyMasked', params: { warmth: -22 } });
    else steps.push({ op: 'adjustments.apply', params: { type: 'white-balance', params: { temperature: -22 } } });
  }
  if (/\bbright(er|en)?\b|\blight(er|en)?\b|\bexpos/.test(p)) {
    if (hasRegion) steps.push({ op: 'adjustments.applyMasked', params: { brightness: 18 } });
    else steps.push({ op: 'adjustments.apply', params: { type: 'brightness-contrast', params: { brightness: 25 } } });
  }
  if (/\bdark(er|en)?\b|\bmoody\b|\bdeeper\b/.test(p)) {
    if (hasRegion) steps.push({ op: 'adjustments.applyMasked', params: { brightness: -16, contrast: 10 } });
    else steps.push({ op: 'adjustments.apply', params: { type: 'brightness-contrast', params: { brightness: -20 } } });
  }
  if (/\bdramatic\b|\bsky\b|\bcontrast(y)?\b|\bvivid\b|\bsaturat|\bmoody\b/.test(p)) {
    if (hasRegion) steps.push({ op: 'adjustments.applyMasked', params: { contrast: 28, saturation: 1.35 } });
    else steps.push({ op: 'adjustments.apply', params: { type: 'vibrance', params: { vibrance: 40 } } });
  }
  if (/\bblur\b|\bdepth\s*of\s*field\b|\bbokeh\b/.test(p)) {
    const radius = /\b(?:slight(?:ly)?|subtle|gently|gentle|a\s+little)\b/.test(p) ? 4
      : /\b(?:bokeh|depth\s*of\s*field|strong(?:ly)?|heavy|blur\s+the\s+background)\b/.test(p) ? 18
        : 10;
    if (hasRegion) steps.push({ op: 'filters.applyMasked', params: { id: 'blur-gaussian', radius, invert: false } });
    else steps.push({ op: 'filters.applyMasked', params: { id: 'blur-gaussian', radius, invert: true, full: true } });
  } else if (/\bsoften\b/.test(p)) {
    // "soften it" on a portrait means soften the skin, never a destructive
    // full-frame blur (frequency separation keeps the pores sharp).
    steps.push({ op: 'retouch.skin', params: { smooth: 22, opacity: 0.7 } });
  }
  if (/\bsharpen\b|\bcrisp(er)?\b/.test(p)) {
    if (hasRegion) steps.push({ op: 'filters.applyMasked', params: { id: 'sharpen', amount: 65 } });
    else steps.push({ op: 'filters.apply', params: { id: 'sharpen' } });
  }
  const wantsAddText = /\b(?:add|create|write|put|insert)\b[^.]*\b(?:title|caption|label|text|word|subtitle)\b/i.test(prompt)
    || /\bwith\s+the\s+(?:text|title|word|caption|label)\b/i.test(prompt)
    || /["'][^"']{1,40}["']/.test(prompt);
  // "add a stroke to the text" applies an effect to EXISTING text — do not
  // create a new text layer for it.
  const existingText = /\b(?:to|on|for|of)\s+the\s+(?:text|title|caption|label|layer|word)\b/i.test(prompt);
  if (wantsAddText && !existingText && !/\b(font|bigger|smaller|larger|colour|color)\b/.test(p)) {
    const qm = prompt.match(/["']([^"']{1,40})["']/);
    const capsM = prompt.match(/\b([A-Z][A-Z0-9]*(?:\s+[A-Z0-9]+){0,3})\b/);
    const afterM = prompt.match(/\b(?:title|caption|label|word|text)\s*[:\-]?\s+([A-Za-z0-9][\w'-]*)/i);
    const afterOk = afterM && !/^(?:and|the|to|at|in|on|for|with|under|over|it|is|text|title|caption|label|word)\b/i.test(afterM[1]);
    let text = qm ? qm[1] : (capsM ? capsM[1] : (afterOk ? afterM[1] : (/\bsubtitle\b/i.test(prompt) ? 'Subtitle' : 'Title')));
    text = String(text).trim().replace(/\s+(?:and|the|to|at|in|on|for|with|under|over|align|then|add)\b.*$/i, '').trim().replace(/[."]+$/, '');
    if (!text) text = /\bsubtitle\b/i.test(prompt) ? 'Subtitle' : 'Title';
    steps.push({ op: 'text.add', params: { text, x: 0, y: 0, color: '#ffffff', size: 64 } });
    steps.push({ op: 'text.layout', params: { anchor: 'top-center', sizePct: 0.13, marginPct: 0.05 } });
  }

  // Flip / rotate — active layer by default, whole document when the prompt
  // names the whole image/canvas/photo (Photoshop parity).
  const wholeImage = /\b(?:whole|entire|full)\s+(?:image|canvas|photo|picture|document)\b/.test(p)
    || (/\b(?:canvas|document)\b/.test(p) && /\b(?:rotate|flip|mirror|turn)\b/.test(p));
  const flipM = prompt.match(/\b(?:flip|mirror)\b/i);
  if (flipM) {
    const axis = /\bvertical(ly)?\b/i.test(p) ? 'v' : 'h';
    steps.push(wholeImage ? { op: 'document.flip', params: { axis } } : { op: 'layer.flip', params: { axis } });
  }
  const rotM = prompt.match(/\b(?:rotate|turn)\b[^\d]*?\b(90|180|270)\b/i);
  if (rotM) {
    const degrees = Number(rotM[1]);
    steps.push(wholeImage ? { op: 'document.rotate', params: { degrees } } : { op: 'layer.rotate', params: { degrees } });
  }

  // Fill a region with a colour (selection fill / paint bucket).
  const FILL_COLORS = 'red|blue|green|yellow|orange|purple|pink|black|white|grey|gray|cyan|magenta|brown|teal|#[0-9a-f]{3,6}';
  const fillM = prompt.match(new RegExp(`\\bfill\\b[^.]*?\\bwith\\s+(${FILL_COLORS})\\b`, 'i'))
    || prompt.match(new RegExp(`\\bfill\\b[^.]*?\\b(${FILL_COLORS})\\b`, 'i'));
  if (fillM) {
    const color = colorForName(fillM[1]);
    if (color) {
      if (hasRegion) steps.push({ op: 'selection.fill', params: { color } });
      else steps.push({ op: 'fill.flood', params: { color, x: 0, y: 0 } });
    }
  }

  // Text: "make the text bigger/smaller", recolour or change content.
  if (/\btext\b|\btitle\b|\bcaption\b|\bfont\b/.test(p) && !/\badd\b/.test(p)) {
    const reuse = {};
    if (/\b(bigger|larger|increase)\b/.test(p)) reuse.size = 96;
    else if (/\b(smaller|reduce|decrease)\b/.test(p)) reuse.size = 28;
    const cm = prompt.match(/\b(?:to|in|make it)\s+(red|blue|green|yellow|orange|purple|pink|black|white|grey|gray|#(?:[0-9a-f]{3,6}))\b/i);
    if (cm) reuse.color = colorForName(cm[1]);
    if (Object.keys(reuse).length) steps.push({ op: 'text.edit', params: reuse });
  }

  // Align a layer / the logo / the selection.
  const alignM = prompt.match(/\balign\b[^.]*?\b(centre|center|left|right|top|middle|bottom)\b/i);
  if (alignM) {
    const map = { centre: 'center', center: 'center', left: 'left', right: 'right', top: 'top', middle: 'middle', bottom: 'bottom' };
    steps.push({ op: 'layers.align', params: { mode: map[alignM[1].toLowerCase()] || 'center' } });
  }

  // Layer styles (effects).
  if (/\bdrop\s*shadow\b/.test(p)) steps.push({ op: 'layers.addStyle', params: { type: 'drop-shadow', params: {} } });
  else if (/\bstroke\b|\boutline\b/.test(p)) steps.push({ op: 'layers.addStyle', params: { type: 'stroke', params: {} } });
  else if (/\bcolo?ur?\s*overlay\b/.test(p)) steps.push({ op: 'layers.addStyle', params: { type: 'color-overlay', params: {} } });

  // Copy / paste the selection.
  if (/\bcopy\b/.test(p) && /\bselection\b|\bselected\b/.test(p)) steps.push({ op: 'selection.copy', params: {} });
  if (/\bpaste\b/.test(p) && /\bselection\b|\bcopied\b/.test(p)) steps.push({ op: 'selection.paste', params: {} });

  // Extend the canvas to a named aspect ratio.
  const extM = prompt.match(/\bextend\b[^.]*?\b(16:9|9:16|4:5|1:1)\b/);
  if (/\bextend\b/.test(p) && extM) {
    const parts = extM[1].split(':').map(Number);
    const ratio = parts[0] / parts[1];
    const w = Number(state.width) || 0, h = Number(state.height) || 0;
    if (w && h) {
      let tw = w, th = h;
      if (w / h < ratio) tw = Math.round(h * ratio); else th = Math.round(w / ratio);
      steps.push({ op: 'document.extend', params: { width: tw, height: th, anchor: 'center' } });
    }
  }

  if (!steps.length) {
    steps.push({ op: 'adjustments.apply', params: { type: 'brightness-contrast', params: { brightness: 12, contrast: 12 } } });
    return { reply: `No deterministic match for: ${prompt}`, steps, planner: 'local', fallback: true };
  }
  const reply = `Planned ${steps.length} editor step${steps.length === 1 ? '' : 's'} for: ${prompt}`;
  return { reply, steps, planner: 'local' };
}

// Ops the model sometimes emits that have no editing effect (or cause side
// effects like saving a gallery copy). They are stripped from planner output.
const NOOP_OPS = new Set(['document.info', 'layers.list', 'history.mark', 'history.undo', 'history.redo', 'export.png', 'export.gallery']);

// Normalize a model plan before it runs: drop no-op/export steps, and if a
// masked op is emitted with no selection.* before it, insert the region
// selection implied by the prompt (so masked edits never target the whole
// layer by accident).
export function sanitizeSteps(steps, prompt = '') {
  const p = String(prompt || '').toLowerCase();
  const region = /\bface\b/.test(p)
    ? { op: 'selection.face', params: {} }
    : /\bsky\b/.test(p)
      ? { op: 'selection.sky', params: {} }
      : /\bbackground\b|\bbehind\b|\bbg\b/.test(p)
        ? { op: 'selection.background', params: {} }
        : { op: 'selection.subject', params: { label: 'person' } };
  const out = [];
  let hasSelection = false;
  for (const s of (Array.isArray(steps) ? steps : [])) {
    if (!s || !s.op || NOOP_OPS.has(s.op)) continue;
    if (String(s.op).startsWith('selection.')) hasSelection = true;
    if (!hasSelection && (s.op === 'adjustments.applyMasked' || s.op === 'filters.applyMasked')) {
      out.push(region);
      hasSelection = true;
    }
    out.push({ op: s.op, params: s.params || {} });
  }
  return out;
}

// Deterministic platform-format guard: when the user asks for a platform or
// aspect format and the plan omits a crop, prepend a centred document.crop for
// the target aspect ratio (models frequently describe the crop but forget the op).
export function formatCropRect(prompt = '', width = 0, height = 0) {
  const p = String(prompt || '').toLowerCase();
  const w = Number(width) || 0, h = Number(height) || 0;
  if (!w || !h) return null;
  let ratio = null;
  const dim = p.match(/(\d{2,5})\s*[x×*]\s*(\d{2,5})/);
  if (dim) ratio = Number(dim[1]) / Number(dim[2]);
  if (!ratio && /instagram|insta|square|profile|avatar|\b1:1\b/.test(p)) ratio = 1;
  else if (!ratio && /story|reel|\b9:16\b/.test(p)) ratio = 9 / 16;
  else if (!ratio && (/\b4:5\b|1080x1350/.test(p))) ratio = 4 / 5;
  else if (!ratio && (/\b16:9\b|widescreen|youtube/.test(p))) ratio = 16 / 9;
  if (!ratio) return null;
  let cw, ch;
  if (w / h > ratio) { ch = h; cw = Math.round(h * ratio); }
  else { cw = w; ch = Math.round(w / ratio); }
  const x = Math.round((w - cw) / 2), y = Math.round((h - ch) / 2);
  return { x, y, width: cw, height: ch };
}

export function ensureFormatCrop(steps, prompt = '', width = 0, height = 0) {
  const list = Array.isArray(steps) ? steps : [];
  if (list.some((s) => s && s.op === 'document.crop')) return list;
  const rect = formatCropRect(prompt, width, height);
  if (!rect) return list;
  return [{ op: 'document.crop', params: rect }, ...list];
}

export function ensureGenericAutoTone(steps, prompt = '') {
  const list = Array.isArray(steps) ? steps : [];
  if (!isGenericIntent(prompt)) return list;
  if (list.some((s) => s && (s.op === 'photo.autoTone' || s.op === 'adjustments.autoTone'))) return list;
  return [{ op: 'photo.autoTone', params: {} }, ...list];
}

export function extractJson(text) {  if (!text) return null;
  const fenced = String(text).match(/```(?:json)?\s*([\s\S]*?)```/i);
  const raw = fenced ? fenced[1] : String(text);
  const start = raw.indexOf('{');
  const end = raw.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try { return JSON.parse(raw.slice(start, end + 1)); } catch (_) { return null; }
}

export function createNaturalLanguageEditor({ control, planUrl = DEFAULT_PLAN_URL, chatUrl = DEFAULT_CHAT_URL, sessionUrl = DEFAULT_SESSION_URL, styleGuideUrl = '/api/editor/style-guide', fetchImpl = null, onStep = null } = {}) {
  if (!control || typeof control.invoke !== 'function') throw new Error('createNaturalLanguageEditor requires a control with invoke()');
  const doFetch = fetchImpl || (typeof fetch !== 'undefined' ? fetch : null);
  let cachedSession = null;
  let guideLoaded = false;
  async function ensureGuide() {
    if (guideLoaded) return;
    guideLoaded = true;
    try { await fetchStyleGuide(styleGuideUrl, doFetch); } catch (_) {}
  }

  async function postJson(url, body) {
    const res = await doFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!res || !res.ok) return null;
    return res.json().catch(() => null);
  }

  // Strategy 1: a dedicated editor planning route (HARNESS /api/editor/plan).
  // The route builds its own system prompt from {prompt, state}; we pass the
  // op catalog + style guide through state.planner_guidance so the model gets
  // the same strong instructions instead of the route's generic default.
  async function requestPlanEndpoint(prompt, state, extra = {}) {
    if (!doFetch || !planUrl) return null;
    try {
      const guidance = extra.mode === 'check' ? `${activeGuidance()} ${CHECK_INSTRUCTION}` : activeGuidance();
      const data = await postJson(planUrl, { prompt, state: { ...state, planner_guidance: guidance }, ...extra });
      if (!data) return null;
      if (Array.isArray(data.steps)) return { reply: data.reply || '', steps: data.steps, planner: data.planner || 'llm', done: !!data.done };
      if (data.done) return { reply: data.reply || 'Looks done.', steps: [], planner: 'llm', done: true };
      return null;
    } catch (_) {
      return null;
    }
  }

  // Strategy 2: reuse the app's existing model endpoint (/api/chat) from the
  // browser with the user's own session. The model plans with vision-less
  // state + the op catalog and returns JSON steps.
  async function requestViaChat(prompt, state, extra = {}) {
    if (!doFetch || !chatUrl) return null;
    try {
      if (!cachedSession) {
        const r = await doFetch(sessionUrl);
        if (r && r.ok) {
          const d = await r.json().catch(() => null);
          cachedSession = d && (d.session?.id || d.session_id || d.id || d.sessionId) || null;
        }
      }
      if (!cachedSession) return null;
      const message = `${activeGuidance()}\n\n${buildPlannerMessage(prompt, state, extra)}`;
      const data = await postJson(chatUrl, { message, session: cachedSession });
      if (!data) return null;
      const parsed = extractJson(data.response || data.message || '');
      if (!parsed || !Array.isArray(parsed.steps)) return null;
      return { reply: parsed.reply || '', steps: parsed.steps, planner: 'llm' };
    } catch (_) {
      return null;
    }
  }

  async function requestPlan(prompt, state, extra = {}) {
    if (!doFetch) return null;
    return (await requestPlanEndpoint(prompt, state, extra)) || (extra.mode ? null : await requestViaChat(prompt, state, extra));
  }

  async function execute(prompt, { useServer = true, maxIterations = 3, onStep: stepCb = null } = {}) {
    const emitStep = stepCb || onStep;
    if (!prompt || !String(prompt).trim()) throw new Error('empty prompt');
    await ensureGuide();
    let docState = {};
    try { docState = (await control.invoke('document.info')).result || {}; } catch (_) {}
    let beforeShot = null;
    try { beforeShot = (await control.invoke('screenshot')).result; } catch (_) {}

    let plan = useServer ? await requestPlan(prompt, docState, beforeShot && beforeShot.dataUrl ? { screenshot: beforeShot.dataUrl } : {}) : null;
    const usedServer = !!plan;
    if (!plan) plan = localPlan(prompt, docState);
    plan = { ...plan, steps: ensureFormatCrop(ensureGenericAutoTone(sanitizeSteps(plan.steps, prompt), prompt), prompt, docState.width, docState.height) };

    let mark = 0;
    try { mark = (await control.invoke('history.mark')).result; } catch (_) {}
    const trace = [];
    let failed = null;
    let reply = plan.reply;
    let applied = [];
    let faceLumBefore = null;
    let faceLumAfter = null;

    const runSteps = async (steps) => {
      applied = [];
      faceLumBefore = null;
      faceLumAfter = null;
      for (const step of steps) {
        const started = Date.now();
        const result = await control.invoke(step.op, step.params || {});
        let shot = null;
        try { shot = (await control.invoke('screenshot')).result; } catch (_) {}
        const entry = { op: step.op, params: step.params || {}, ok: !!(result && result.ok), error: result && result.error ? result.error : null, ms: Date.now() - started, screenshot: shot && shot.dataUrl ? shot.dataUrl : null };
        trace.push(entry);
        if (emitStep) { try { emitStep(entry, trace.length, steps.length); } catch (_) {} }
        if (!entry.ok) { failed = entry; return false; }
        // Face-brighten check: capture face-region luminance right after the face
        // selection and again after the first masked edit on that selection.
        if (entry.ok && (step.op === 'selection.face' || step.op === 'adjustments.applyMasked' || step.op === 'filters.applyMasked')) {
          try {
            const lum = (await control.invoke('selection.regionLuminance')).result;
            const value = lum && lum.luminance != null ? lum.luminance : null;
            if (value != null) {
              if (step.op === 'selection.face' && faceLumBefore == null) faceLumBefore = value;
              else if (step.op !== 'selection.face' && faceLumBefore != null && faceLumAfter == null) faceLumAfter = value;
            }
          } catch (_) {}
        }
        applied.push({ op: step.op, params: step.params || {} });
      }
      return true;
    };

    await runSteps(plan.steps);

    // Self-check loop (server planners only): show the model the BEFORE and
    // AFTER images plus the intent; if it is not satisfied it returns a FULL
    // corrected plan and we REVERT the previous attempt (undo its history
    // entries) before applying it, so corrections never stack on top.
    if (usedServer && !failed) {
      let roundMark = mark;
      for (let i = 0; i < maxIterations; i++) {
        let shot = null;
        try { shot = (await control.invoke('screenshot')).result; } catch (_) {}
        if (!shot || !shot.dataUrl) break;
        let check = null;
        const checkExtra = { mode: 'check', screenshot: shot.dataUrl, applied };
        if (beforeShot && beforeShot.dataUrl) checkExtra.before = beforeShot.dataUrl;
        if (faceLumBefore != null || faceLumAfter != null) checkExtra.faceLuminance = { before: faceLumBefore, after: faceLumAfter };
        try { check = await requestPlan(prompt, { ...docState, iteration: i + 1 }, checkExtra); } catch (_) {}
        if (!check || check.done || !Array.isArray(check.steps) || !check.steps.length) break;
        const next = ensureFormatCrop(sanitizeSteps(check.steps, prompt), prompt, docState.width, docState.height);
        // If the correction would run nothing (only no-op/export steps), keep
        // the current state instead of reverting to a net-zero result.
        if (!next.length) break;
        if (check.reply) reply = check.reply;
        // Revert the attempt that produced this after image.
        let nowMark = 0;
        try { nowMark = (await control.invoke('history.mark')).result; } catch (_) {}
        const entries = Math.max(0, (Number(nowMark) || 0) - (Number(roundMark) || 0));
        for (let k = 0; k < entries; k++) { try { await control.invoke('history.undo'); } catch (_) {} }
        try { roundMark = (await control.invoke('history.mark')).result; } catch (_) {}
        const ok = await runSteps(next);
        if (!ok) break;
      }
    }

    try { await control.invoke('history.collapse', { mark, label: `AI edit: ${String(prompt).slice(0, 60)}` }); } catch (_) {}
    return { ok: !failed, prompt, reply, planner: plan.planner, basicMode: plan.planner !== 'llm', steps: plan.steps, trace, failed };
  }

  return { planUrl, chatUrl, localPlan, requestPlan, requestViaChat, execute };
}

export default createNaturalLanguageEditor;
