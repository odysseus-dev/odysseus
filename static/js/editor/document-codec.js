/**
 * Lossless, versioned editor document serialization.
 *
 * Draft autosave and downloaded project files must use this same boundary.
 * Keeping a second hand-written layer shape is how masks and adjustments were
 * previously dropped when a document was reopened.
 */
import { normalizeAdjustmentData } from './adjustment-layer.js';
import { normalizeEffect } from './effects.js';
import { normalizeMaskDensity, normalizeMaskFeather } from './mask-utils.js';

export const EDITOR_DOCUMENT_VERSION = 15;
export const EDITOR_PROJECT_MAX_BYTES = 256 * 1024 * 1024;
export const EDITOR_MAX_DIMENSION = 32768;
export const EDITOR_MAX_DOCUMENT_PIXELS = 100_000_000;
export const EDITOR_MAX_SURFACE_PIXELS = 300_000_000;
export const EDITOR_MAX_LAYERS = 256;
export const EDITOR_MAX_MASKS_PER_LAYER = 64;
export const EDITOR_MAX_GROUPS = 128;
export const EDITOR_MAX_SAVED_SELECTIONS = 64;

const MAX_SERIALIZED_GUIDES = 1000;
const SUPPORTED_BLEND_MODES = new Set([
  'source-over', 'multiply', 'screen', 'overlay', 'soft-light', 'hard-light',
  'darken', 'lighten', 'color-dodge', 'color-burn', 'difference', 'exclusion',
  'hue', 'saturation', 'color', 'luminosity',
]);

function normalizeGuideValues(values, fallback = []) {
  const source = Array.isArray(values) ? values : fallback;
  const normalized = [];
  for (const raw of Array.isArray(source) ? source : []) {
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) continue;
    if (!normalized.some(existing => Math.abs(existing - value) < 0.0001)) normalized.push(value);
    if (normalized.length >= MAX_SERIALIZED_GUIDES) break;
  }
  return normalized.sort((a, b) => a - b);
}

export function normalizeEditorView(view, fallback = {}) {
  const source = view && typeof view === 'object' ? view : {};
  const fallbackGuides = fallback.guides && typeof fallback.guides === 'object'
    ? fallback.guides
    : { vertical: [], horizontal: [] };
  const guides = source.guides && typeof source.guides === 'object' ? source.guides : {};
  const rawGridSize = Number(source.gridSize ?? fallback.gridSize ?? 16);
  return {
    rulersVisible: Boolean(source.rulersVisible ?? fallback.rulersVisible ?? true),
    gridVisible: Boolean(source.gridVisible ?? fallback.gridVisible ?? false),
    gridSize: Math.max(2, Math.min(1000, Math.round(Number.isFinite(rawGridSize) ? rawGridSize : 16))),
    snapEnabled: Boolean(source.snapEnabled ?? fallback.snapEnabled ?? false),
    snapToGrid: Boolean(source.snapToGrid ?? fallback.snapToGrid ?? false),
    guides: {
      vertical: normalizeGuideValues(guides.vertical, fallbackGuides.vertical),
      horizontal: normalizeGuideValues(guides.horizontal, fallbackGuides.horizontal),
    },
  };
}

export function cloneDocumentValue(value, fallback) {
  try {
    return value == null ? fallback : JSON.parse(JSON.stringify(value));
  } catch {
    return fallback;
  }
}

export class EditorDocumentError extends Error {
  constructor(message, code = 'invalid-document') {
    super(message);
    this.name = 'EditorDocumentError';
    this.code = code;
  }
}

const MIGRATIONS = new Map([
  [1, document => ({
    ...document,
    v: 2,
    layers: (document.layers || []).map(layer => ({
      visible: true,
      opacity: 1,
      locked: false,
      offset: { x: 0, y: 0 },
      ...layer,
      dataUrl: layer?.dataUrl || layer?.dataURL || null,
    })),
  })],
  [2, document => ({
    ...document,
    v: 3,
    layers: (document.layers || []).map(layer => ({
      adjustments: {},
      adjLayers: [],
      activeMaskId: null,
      masks: [],
      ...layer,
    })),
  })],
  [3, document => ({
    ...document,
    v: 4,
    layers: (document.layers || []).map(layer => ({
      kind: 'raster',
      text: null,
      ...layer,
      masks: (layer?.masks || []).map(mask => ({
        mode: 'selection',
        ...mask,
        space: mask?.space || (mask?.mode === 'layer' ? 'layer' : 'document'),
      })),
    })),
  })],
  [4, document => ({
    ...document,
    v: 5,
    view: normalizeEditorView(document.view),
  })],
  [5, document => ({
    ...document,
    v: 6,
    groups: [],
  })],
  [6, document => ({
    ...document,
    v: 7,
    layers: (document.layers || []).map(layer => ({ clipped: false, ...layer })),
  })],
  [7, document => ({
    ...document,
    v: 8,
    groups: (document.groups || []).map(group => ({ parentId: null, ...group })),
  })],
  [8, document => ({
    ...document,
    v: 9,
    groups: (document.groups || []).map(group => ({ masks: [], activeMaskId: null, ...group })),
  })],
  [9, document => ({
    ...document,
    v: 10,
    layers: (document.layers || []).map(layer => ({
      locks: { pixels: false, transparency: false, position: false },
      ...layer,
    })),
  })],
  [10, document => ({
    ...document,
    v: 11,
    savedSelections: [],
  })],
  [11, document => ({
    ...document,
    v: 12,
    layers: (document.layers || []).map(layer => ({
      ...layer,
      masks: (layer.masks || []).map(mask => mask?.mode === 'layer' ? {
        linked: true,
        offset: { x: 0, y: 0 },
        ...mask,
      } : mask),
    })),
  })],
  [12, document => ({
    ...document,
    v: 13,
    layers: (document.layers || []).map(layer => ({ placed: null, ...layer })),
  })],
  [13, document => ({
    ...document,
    v: 14,
    layers: (document.layers || []).map(layer => ({ adjustment: null, ...layer })),
  })],
  [14, document => ({
    ...document,
    v: 15,
    layers: (document.layers || []).map(layer => ({ effects: [], ...layer })),
    groups: (document.groups || []).map(group => ({ effects: [], ...group })),
  })],
]);

function migrateEditorDocument(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new EditorDocumentError('Project root must be a JSON object.');
  }
  const storedVersion = raw.v == null ? 1 : Number(raw.v);
  if (!Number.isInteger(storedVersion) || storedVersion < 1) {
    throw new EditorDocumentError('Project has an invalid document version.', 'invalid-version');
  }
  if (storedVersion > EDITOR_DOCUMENT_VERSION) {
    throw new EditorDocumentError(
      `This project uses version ${storedVersion}; this editor supports up to version ${EDITOR_DOCUMENT_VERSION}.`,
      'future-version',
    );
  }
  let document = { ...raw };
  while (document.v == null || document.v < EDITOR_DOCUMENT_VERSION) {
    const version = document.v == null ? 1 : document.v;
    const migrate = MIGRATIONS.get(version);
    if (!migrate) throw new EditorDocumentError(`No migration is available for project version ${version}.`, 'missing-migration');
    document = migrate(document);
  }
  return { document, storedVersion };
}

function positiveDimension(value, fallback = 0) {
  const numeric = Math.round(Number(value ?? fallback));
  return Number.isFinite(numeric) && numeric > 0 && numeric <= EDITOR_MAX_DIMENSION ? numeric : null;
}

function isEmbeddedPng(value) {
  return typeof value === 'string' && value.startsWith('data:image/png;base64,');
}

function finiteCoordinate(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && Math.abs(numeric) <= 1_000_000 ? numeric : fallback;
}

function normalizePlacedRecord(source) {
  if (!source || typeof source !== 'object' || Array.isArray(source)) return null;
  const sourceWidth = positiveDimension(source.sourceWidth);
  const sourceHeight = positiveDimension(source.sourceHeight);
  const matrix = Array.isArray(source.matrix) && source.matrix.length === 6
    ? source.matrix.map(Number)
    : null;
  if (!sourceWidth || !sourceHeight || !matrix?.every(Number.isFinite) || !isEmbeddedPng(source.sourceDataUrl)) {
    return null;
  }
  if (matrix.some(value => Math.abs(value) > 1_000_000)) return null;
  return {
    sourceWidth,
    sourceHeight,
    sourceName: typeof source.sourceName === 'string'
      ? source.sourceName.slice(0, 200)
      : 'Placed image',
    sourceDataUrl: source.sourceDataUrl,
    matrix,
  };
}

/** Migrate and validate untrusted draft/project JSON before allocating canvases. */
export function prepareEditorDocument(raw) {
  const { document: migrated, storedVersion } = migrateEditorDocument(raw);
  const warnings = [];
  const warn = message => {
    if (warnings.length < 50) warnings.push(message);
  };
  const width = positiveDimension(migrated.imgWidth);
  const height = positiveDimension(migrated.imgHeight);
  if (!width || !height) {
    throw new EditorDocumentError(`Project dimensions must be between 1 and ${EDITOR_MAX_DIMENSION} pixels.`, 'invalid-dimensions');
  }
  if (width * height > EDITOR_MAX_DOCUMENT_PIXELS) {
    throw new EditorDocumentError('Project canvas exceeds the 100 megapixel safety limit.', 'pixel-budget');
  }
  if (!Array.isArray(migrated.layers)) {
    throw new EditorDocumentError('Project is missing its layer list.', 'invalid-layers');
  }
  if (migrated.layers.length > EDITOR_MAX_LAYERS) {
    throw new EditorDocumentError(`Project exceeds the ${EDITOR_MAX_LAYERS}-layer limit.`, 'layer-budget');
  }

  let surfacePixels = width * height;
  let embeddedCharacters = 0;
  let recoveredId = 1;
  const usedIds = new Set();
  const layers = [];
  for (let index = 0; index < migrated.layers.length; index += 1) {
    const source = migrated.layers[index];
    const label = typeof source?.name === 'string' && source.name.trim()
      ? source.name.trim().slice(0, 200)
      : `Layer ${index + 1}`;
    if (!source || typeof source !== 'object' || Array.isArray(source)) {
      warn(`${label} was skipped because its record is invalid.`);
      continue;
    }
    const canvasW = positiveDimension(source.canvasW, width);
    const canvasH = positiveDimension(source.canvasH, height);
    if (!canvasW || !canvasH) {
      warn(`${label} was skipped because its canvas dimensions are invalid.`);
      continue;
    }
    const textIsValid = source.kind === 'text' && source.text && typeof source.text === 'object' && !Array.isArray(source.text);
    const shapeIsValid = source.kind === 'shape' && source.shape && typeof source.shape === 'object' && !Array.isArray(source.shape);
    const adjustmentIsValid = source.kind === 'adjustment' && source.adjustment && typeof source.adjustment === 'object' && !Array.isArray(source.adjustment);
    const placed = source.kind === 'placed' ? normalizePlacedRecord(source.placed) : null;
    const placedIsValid = source.kind === 'placed' && !!placed;
    if (source.kind && source.kind !== 'raster' && source.kind !== 'text' && source.kind !== 'shape' && source.kind !== 'placed' && source.kind !== 'adjustment') {
      warn(`${label} used unsupported layer type "${String(source.kind).slice(0, 40)}" and was recovered as raster pixels.`);
    } else if (source.kind === 'text' && !textIsValid) {
      warn(`${label} had invalid editable text metadata and was recovered as raster pixels.`);
    } else if (source.kind === 'shape' && !shapeIsValid) {
      warn(`${label} had invalid editable shape metadata and was recovered as raster pixels.`);
    } else if (source.kind === 'adjustment' && !adjustmentIsValid) {
      warn(`${label} had invalid adjustment metadata and was recovered as raster pixels.`);
    } else if (source.kind === 'placed' && !placedIsValid) {
      warn(`${label} had a missing or corrupt placed source and was recovered from its raster preview.`);
    }
    if (!isEmbeddedPng(source.dataUrl) && !textIsValid && !shapeIsValid && !placedIsValid && !adjustmentIsValid) {
      warn(`${label} was skipped because its pixel data is missing or corrupt.`);
      continue;
    }
    if (!isEmbeddedPng(source.dataUrl) && textIsValid) {
      warn(`${label} had no valid preview pixels and will be rebuilt from its editable text data.`);
    }
    if (!isEmbeddedPng(source.dataUrl) && shapeIsValid) {
      warn(`${label} had no valid preview pixels and will be rebuilt from its editable shape data.`);
    }
    if (!isEmbeddedPng(source.dataUrl) && placedIsValid) {
      warn(`${label} had no valid preview pixels and will be rebuilt from its placed source.`);
    }
    embeddedCharacters += typeof source.dataUrl === 'string' ? source.dataUrl.length : 0;
    surfacePixels += canvasW * canvasH;
    if (placedIsValid) {
      embeddedCharacters += placed.sourceDataUrl.length;
      surfacePixels += placed.sourceWidth * placed.sourceHeight;
    }
    let id = typeof source.id === 'string' && source.id.trim() ? source.id.trim().slice(0, 100) : '';
    if (!id || usedIds.has(id)) {
      do { id = `layer-recovered-${recoveredId++}`; } while (usedIds.has(id));
      warn(`${label} received a replacement layer id.`);
    }
    usedIds.add(id);
    const masks = [];
    const maskRecords = Array.isArray(source.masks) ? source.masks : [];
    if (maskRecords.length > EDITOR_MAX_MASKS_PER_LAYER) {
      warn(`${label} has more than ${EDITOR_MAX_MASKS_PER_LAYER} masks; extras were skipped.`);
    }
    for (let maskIndex = 0; maskIndex < Math.min(maskRecords.length, EDITOR_MAX_MASKS_PER_LAYER); maskIndex += 1) {
      const mask = maskRecords[maskIndex];
      const maskLabel = typeof mask?.name === 'string' && mask.name.trim() ? mask.name.trim().slice(0, 200) : `Mask ${maskIndex + 1}`;
      const maskW = positiveDimension(mask?.canvasW, width);
      const maskH = positiveDimension(mask?.canvasH, height);
      if (!mask || typeof mask !== 'object' || !maskW || !maskH || !isEmbeddedPng(mask.dataUrl)) {
        warn(`${label} / ${maskLabel} was skipped because its mask data is invalid.`);
        continue;
      }
      embeddedCharacters += mask.dataUrl.length;
      surfacePixels += maskW * maskH;
      let maskId = typeof mask.id === 'string' && mask.id.trim() ? mask.id.trim().slice(0, 100) : '';
      if (!maskId || usedIds.has(maskId)) {
        do { maskId = `mask-recovered-${recoveredId++}`; } while (usedIds.has(maskId));
        warn(`${label} / ${maskLabel} received a replacement mask id.`);
      }
      usedIds.add(maskId);
      const mode = mask.mode === 'layer' ? 'layer' : 'selection';
      masks.push({
        ...mask,
        id: maskId,
        name: maskLabel,
        visible: mask.visible !== false,
        density: normalizeMaskDensity(mask.density),
        feather: normalizeMaskFeather(mask.feather),
        mode,
        space: mask.space === 'layer' || mask.space === 'document'
          ? mask.space
          : (mode === 'layer' ? 'layer' : 'document'),
        linked: mode === 'layer' ? mask.linked !== false : true,
        offset: mode === 'layer' ? {
          x: finiteCoordinate(mask.offset?.x),
          y: finiteCoordinate(mask.offset?.y),
        } : { x: 0, y: 0 },
        canvasW: maskW,
        canvasH: maskH,
      });
    }

    if (surfacePixels > EDITOR_MAX_SURFACE_PIXELS) {
      throw new EditorDocumentError('Project layers and masks exceed the safe in-memory pixel budget.', 'surface-budget');
    }
    if (embeddedCharacters > EDITOR_PROJECT_MAX_BYTES) {
      throw new EditorDocumentError('Project embedded image data exceeds the 256 MB safety limit.', 'data-budget');
    }
    const offset = source.offset && typeof source.offset === 'object' ? source.offset : {};
    const opacity = Number(source.opacity);
    const blendMode = SUPPORTED_BLEND_MODES.has(source.blendMode) ? source.blendMode : 'source-over';
    if (source.blendMode && blendMode !== source.blendMode) {
      warn(`${label} used unsupported blend mode "${String(source.blendMode).slice(0, 40)}" and was reset to Normal.`);
    }
    layers.push({
      ...source,
      id,
      name: label,
      visible: source.visible !== false,
      opacity: Number.isFinite(opacity) ? Math.max(0, Math.min(1, opacity)) : 1,
      locked: !!source.locked,
      locks: {
        pixels: !!source.locks?.pixels,
        transparency: !!source.locks?.transparency,
        position: !!source.locks?.position,
      },
      clipped: !!source.clipped,
      blendMode,
      kind: textIsValid ? 'text' : (shapeIsValid ? 'shape' : (placedIsValid ? 'placed' : (adjustmentIsValid ? 'adjustment' : 'raster'))),
      text: textIsValid ? source.text : null,
      shape: shapeIsValid ? source.shape : null,
      adjustment: adjustmentIsValid ? normalizeAdjustmentData(source.adjustment) : null,
      effects: Array.isArray(source.effects) ? source.effects.map(normalizeEffect) : [],
      placed: placedIsValid ? placed : null,
      canvasW,
      canvasH,
      offset: { x: finiteCoordinate(offset.x), y: finiteCoordinate(offset.y) },
      masks,
      activeMaskId: masks.some(mask => mask.id === source.activeMaskId) ? source.activeMaskId : null,
      adjustments: source.adjustments && typeof source.adjustments === 'object' ? source.adjustments : {},
      adjLayers: Array.isArray(source.adjLayers) ? source.adjLayers : [],
    });
  }
  if (!layers.length) {
    throw new EditorDocumentError('No recoverable layers were found in this project.', 'no-layers');
  }

  const activeLayerId = layers.some(layer => layer.id === migrated.activeLayerId)
    ? migrated.activeLayerId
    : layers[layers.length - 1].id;
  const groups = [];
  const claimedLayerIds = new Set();
  const validLayerIds = new Set(layers.map(layer => layer.id));
  const groupRecords = Array.isArray(migrated.groups) ? migrated.groups : [];
  if (groupRecords.length > EDITOR_MAX_GROUPS) {
    warn(`Project has more than ${EDITOR_MAX_GROUPS} groups; extras were skipped.`);
  }
  for (let index = 0; index < Math.min(groupRecords.length, EDITOR_MAX_GROUPS); index += 1) {
    const source = groupRecords[index];
    if (!source || typeof source !== 'object' || Array.isArray(source)) {
      warn(`Group ${index + 1} was skipped because its record is invalid.`);
      continue;
    }
    const layerIds = [];
    for (const id of Array.isArray(source.layerIds) ? source.layerIds : []) {
      if (validLayerIds.has(id) && !claimedLayerIds.has(id)) {
        claimedLayerIds.add(id);
        layerIds.push(id);
      }
    }
    let id = typeof source.id === 'string' && source.id.trim() ? source.id.trim().slice(0, 100) : '';
    if (!id || usedIds.has(id)) {
      do { id = `group-recovered-${recoveredId++}`; } while (usedIds.has(id));
      warn(`${String(source.name || `Group ${index + 1}`).slice(0, 200)} received a replacement group id.`);
    }
    usedIds.add(id);
    const groupMasks = [];
    const groupMaskRecords = Array.isArray(source.masks) ? source.masks : [];
    if (groupMaskRecords.length > EDITOR_MAX_MASKS_PER_LAYER) {
      warn(`${String(source.name || `Group ${index + 1}`).slice(0, 200)} has too many masks; extras were skipped.`);
    }
    for (let maskIndex = 0; maskIndex < Math.min(groupMaskRecords.length, EDITOR_MAX_MASKS_PER_LAYER); maskIndex += 1) {
      const mask = groupMaskRecords[maskIndex];
      const maskLabel = typeof mask?.name === 'string' && mask.name.trim()
        ? mask.name.trim().slice(0, 200)
        : `Group Mask ${maskIndex + 1}`;
      const maskW = positiveDimension(mask?.canvasW, width);
      const maskH = positiveDimension(mask?.canvasH, height);
      if (!mask || typeof mask !== 'object' || !maskW || !maskH || !isEmbeddedPng(mask.dataUrl)) {
        warn(`${String(source.name || `Group ${index + 1}`).slice(0, 200)} / ${maskLabel} was skipped because its mask data is invalid.`);
        continue;
      }
      embeddedCharacters += mask.dataUrl.length;
      surfacePixels += maskW * maskH;
      let maskId = typeof mask.id === 'string' && mask.id.trim() ? mask.id.trim().slice(0, 100) : '';
      if (!maskId || usedIds.has(maskId)) {
        do { maskId = `mask-recovered-${recoveredId++}`; } while (usedIds.has(maskId));
        warn(`${String(source.name || `Group ${index + 1}`).slice(0, 200)} / ${maskLabel} received a replacement mask id.`);
      }
      usedIds.add(maskId);
      groupMasks.push({
        id: maskId,
        name: maskLabel,
        visible: mask.visible !== false,
        density: normalizeMaskDensity(mask.density),
        feather: normalizeMaskFeather(mask.feather),
        mode: 'group',
        space: 'document',
        canvasW: maskW,
        canvasH: maskH,
        dataUrl: mask.dataUrl,
      });
    }
    const opacity = Number(source.opacity);
    const blendMode = SUPPORTED_BLEND_MODES.has(source.blendMode) ? source.blendMode : 'source-over';
    if (source.blendMode && blendMode !== source.blendMode) {
      warn(`${String(source.name || `Group ${index + 1}`).slice(0, 200)} used an unsupported blend mode and was reset to Normal.`);
    }
    groups.push({
      id,
      name: String(source.name || `Group ${index + 1}`).trim().slice(0, 200) || `Group ${index + 1}`,
      layerIds,
      parentId: typeof source.parentId === 'string' && source.parentId.trim()
        ? source.parentId.trim().slice(0, 100)
        : null,
      visible: source.visible !== false,
      opacity: Number.isFinite(opacity) ? Math.max(0, Math.min(1, opacity)) : 1,
      blendMode,
      locked: !!source.locked,
      collapsed: !!source.collapsed,
      ...(Array.isArray(source.effects) && source.effects.length
        ? { effects: source.effects.map(serializeEffect) }
        : {}),
      masks: groupMasks,
      activeMaskId: groupMasks.some(mask => mask.id === source.activeMaskId) ? source.activeMaskId : null,
    });
  }
  if (surfacePixels > EDITOR_MAX_SURFACE_PIXELS) {
    throw new EditorDocumentError('Project layers and masks exceed the safe in-memory pixel budget.', 'surface-budget');
  }
  if (embeddedCharacters > EDITOR_PROJECT_MAX_BYTES) {
    throw new EditorDocumentError('Project embedded image data exceeds the 256 MB safety limit.', 'data-budget');
  }
  const savedSelections = [];
  const selectionRecords = Array.isArray(migrated.savedSelections) ? migrated.savedSelections : [];
  if (selectionRecords.length > EDITOR_MAX_SAVED_SELECTIONS) {
    warn(`Project has more than ${EDITOR_MAX_SAVED_SELECTIONS} saved selections; extras were skipped.`);
  }
  for (let index = 0; index < Math.min(selectionRecords.length, EDITOR_MAX_SAVED_SELECTIONS); index += 1) {
    const source = selectionRecords[index];
    const name = typeof source?.name === 'string' && source.name.trim()
      ? source.name.trim().slice(0, 100)
      : `Selection ${index + 1}`;
    if (!source || typeof source !== 'object' || source.canvasW !== width || source.canvasH !== height || !isEmbeddedPng(source.dataUrl)) {
      warn(`${name} was skipped because its saved selection data is invalid or has the wrong dimensions.`);
      continue;
    }
    embeddedCharacters += source.dataUrl.length;
    surfacePixels += width * height;
    let id = typeof source.id === 'string' && source.id.trim() ? source.id.trim().slice(0, 100) : '';
    if (!id || usedIds.has(id)) {
      do { id = `selection-recovered-${recoveredId++}`; } while (usedIds.has(id));
      warn(`${name} received a replacement selection id.`);
    }
    usedIds.add(id);
    savedSelections.push({ id, name, canvasW: width, canvasH: height, dataUrl: source.dataUrl });
  }
  if (surfacePixels > EDITOR_MAX_SURFACE_PIXELS) {
    throw new EditorDocumentError('Project layers, masks, and saved selections exceed the safe in-memory pixel budget.', 'surface-budget');
  }
  if (embeddedCharacters > EDITOR_PROJECT_MAX_BYTES) {
    throw new EditorDocumentError('Project embedded image data exceeds the 256 MB safety limit.', 'data-budget');
  }
  const validGroupIds = new Set(groups.map(group => group.id));
  const groupsById = new Map(groups.map(group => [group.id, group]));
  for (const group of groups) {
    if (group.parentId === group.id || (group.parentId && !validGroupIds.has(group.parentId))) {
      warn(`${group.name} referenced a missing or invalid parent group and was moved to the top level.`);
      group.parentId = null;
    }
  }
  for (const group of groups) {
    const visited = new Set([group.id]);
    let cursor = group;
    while (cursor.parentId) {
      if (visited.has(cursor.parentId)) {
        warn(`${group.name} contained a cyclic group relationship and was moved to the top level.`);
        group.parentId = null;
        break;
      }
      visited.add(cursor.parentId);
      cursor = groupsById.get(cursor.parentId);
      if (!cursor) break;
    }
  }
  // A group may contain only child groups. Remove only empty leaves after the
  // complete parent graph is available, then repeat for newly empty parents.
  while (true) {
    const parentIds = new Set(groups.map(group => group.parentId).filter(Boolean));
    const emptyIndex = groups.findIndex(group => !group.layerIds.length && !parentIds.has(group.id));
    if (emptyIndex < 0) break;
    const [empty] = groups.splice(emptyIndex, 1);
    warn(`${empty.name} was skipped because it has no recoverable layers or child groups.`);
  }
  const scopeByLayer = new Map();
  for (const group of groups) {
    for (const id of group.layerIds) scopeByLayer.set(id, group.id);
  }
  for (let index = 0; index < layers.length; index += 1) {
    const layer = layers[index];
    if (!layer.clipped) continue;
    const previous = layers[index - 1];
    const scope = scopeByLayer.get(layer.id) || null;
    if (!previous || (scopeByLayer.get(previous.id) || null) !== scope) {
      layer.clipped = false;
      warn(`${layer.name} had no clipping base in its layer scope and was released.`);
    }
  }
  if (storedVersion < EDITOR_DOCUMENT_VERSION) {
    warnings.unshift(`Project upgraded from version ${storedVersion} to ${EDITOR_DOCUMENT_VERSION}.`);
  }
  return {
    document: {
      ...migrated,
      v: EDITOR_DOCUMENT_VERSION,
      imgWidth: width,
      imgHeight: height,
      activeLayerId,
      view: normalizeEditorView(migrated.view),
      groups,
      savedSelections,
      layers,
    },
    warnings,
    migratedFrom: storedVersion < EDITOR_DOCUMENT_VERSION ? storedVersion : null,
  };
}

function canvasDataUrl(canvas) {
  if (!canvas || typeof canvas.toDataURL !== 'function') return null;
  try {
    return canvas.toDataURL('image/png');
  } catch {
    return null;
  }
}

function serializeEffect(effect) {
  const normalized = normalizeEffect(effect);
  if (!normalized.mask) return normalized;
  return {
    ...normalized,
    mask: {
      id: normalized.mask.id,
      name: normalized.mask.name,
      visible: normalized.mask.visible !== false,
      canvasW: normalized.mask.canvas?.width || normalized.mask.canvasW || 0,
      canvasH: normalized.mask.canvas?.height || normalized.mask.canvasH || 0,
      dataUrl: canvasDataUrl(normalized.mask.canvas),
    },
  };
}

export function serializeEditorDocument(state, extras = {}) {
  return {
    v: EDITOR_DOCUMENT_VERSION,
    imageId: state.imageId || null,
    imgWidth: state.imgWidth,
    imgHeight: state.imgHeight,
    activeLayerId: state.activeLayerId || null,
    nextLayerId: state.nextLayerId,
    view: normalizeEditorView(state),
    ...extras,
    savedSelections: (state.savedSelections || []).slice(0, EDITOR_MAX_SAVED_SELECTIONS).map(selection => ({
      id: selection.id,
      name: String(selection.name || 'Selection').slice(0, 100),
      canvasW: selection.canvas?.width || state.imgWidth || 0,
      canvasH: selection.canvas?.height || state.imgHeight || 0,
      dataUrl: canvasDataUrl(selection.canvas),
    })),
    groups: (state.layerGroups || []).map(group => ({
      id: group.id,
      name: group.name,
      layerIds: [...(group.layerIds || [])],
      parentId: group.parentId || null,
      visible: group.visible !== false,
      opacity: typeof group.opacity === 'number' ? group.opacity : 1,
      blendMode: group.blendMode || 'source-over',
      locked: !!group.locked,
      collapsed: !!group.collapsed,
      activeMaskId: group.activeMaskId || null,
      ...(Array.isArray(group.effects) && group.effects.length
        ? { effects: group.effects.map(serializeEffect) }
        : {}),
      masks: (group.masks || []).map(mask => ({
        id: mask.id,
        name: mask.name,
        visible: mask.visible !== false,
        density: normalizeMaskDensity(mask.density),
        feather: normalizeMaskFeather(mask.feather),
        mode: 'group',
        space: 'document',
        canvasW: mask.canvas?.width || state.imgWidth || 0,
        canvasH: mask.canvas?.height || state.imgHeight || 0,
        dataUrl: canvasDataUrl(mask.canvas),
      })),
    })),
    layers: (state.layers || []).map(layer => ({
      id: layer.id,
      name: layer.name,
      visible: layer.visible !== false,
      opacity: typeof layer.opacity === 'number' ? layer.opacity : 1,
      locked: !!layer.locked,
      locks: {
        pixels: !!layer.locks?.pixels,
        transparency: !!layer.locks?.transparency,
        position: !!layer.locks?.position,
      },
      clipped: !!layer.clipped,
      isBase: !!layer.isBase,
      blendMode: layer.blendMode || 'source-over',
      kind: layer.kind || 'raster',
      text: cloneDocumentValue(layer.text, null),
      shape: cloneDocumentValue(layer.shape, null),
      adjustment: cloneDocumentValue(layer.adjustment, null),
      effects: Array.isArray(layer.effects) ? layer.effects.map(serializeEffect) : [],
      placed: layer.kind === 'placed' && layer.placed?.sourceCanvas ? {
        sourceWidth: layer.placed.sourceWidth || layer.placed.sourceCanvas.width,
        sourceHeight: layer.placed.sourceHeight || layer.placed.sourceCanvas.height,
        sourceName: String(layer.placed.sourceName || 'Placed image').slice(0, 200),
        sourceDataUrl: canvasDataUrl(layer.placed.sourceCanvas),
        matrix: Array.isArray(layer.placed.matrix) ? layer.placed.matrix.map(Number) : [1, 0, 0, 1, 0, 0],
      } : null,
      canvasW: layer.canvas?.width || 0,
      canvasH: layer.canvas?.height || 0,
      offset: { ...(state.layerOffsets?.get(layer.id) || { x: 0, y: 0 }) },
      dataUrl: canvasDataUrl(layer.canvas),
      adjustments: cloneDocumentValue(layer.adjustments, {}),
      adjLayers: cloneDocumentValue(layer.adjLayers, []),
      activeMaskId: layer.activeMaskId || null,
      masks: (layer.masks || []).map(mask => ({
        id: mask.id,
        name: mask.name,
        visible: mask.visible !== false,
        density: normalizeMaskDensity(mask.density),
        feather: normalizeMaskFeather(mask.feather),
        // Existing masks are AI/selection regions. A future true layer mask
        // uses mode="layer" without changing old saved documents.
        mode: mask.mode || 'selection',
        space: mask.space || (mask.mode === 'layer' ? 'layer' : 'document'),
        linked: mask.mode === 'layer' ? mask.linked !== false : true,
        offset: mask.mode === 'layer'
          ? { x: finiteCoordinate(mask.offset?.x), y: finiteCoordinate(mask.offset?.y) }
          : { x: 0, y: 0 },
        canvasW: mask.canvas?.width || state.imgWidth || 0,
        canvasH: mask.canvas?.height || state.imgHeight || 0,
        dataUrl: canvasDataUrl(mask.canvas),
      })),
    })),
  };
}

export function nextLayerIdFromDocument(data) {
  const stored = Number(data?.nextLayerId);
  if (Number.isInteger(stored) && stored > 0) return stored;
  let max = 0;
  for (const layer of data?.layers || []) {
    const match = String(layer?.id || '').match(/(\d+)$/);
    if (match) max = Math.max(max, Number(match[1]));
    for (const mask of layer?.masks || []) {
      const maskMatch = String(mask?.id || '').match(/(\d+)$/);
      if (maskMatch) max = Math.max(max, Number(maskMatch[1]));
    }
  }
  for (const group of data?.groups || []) {
    const match = String(group?.id || '').match(/(\d+)$/);
    if (match) max = Math.max(max, Number(match[1]));
    for (const mask of group?.masks || []) {
      const maskMatch = String(mask?.id || '').match(/(\d+)$/);
      if (maskMatch) max = Math.max(max, Number(maskMatch[1]));
    }
  }
  return max + 1;
}
