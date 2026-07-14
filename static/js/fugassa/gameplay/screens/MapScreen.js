import * as api from '../../fugassaApi.js';
import { escapeHtml } from './InventoryScreen.js';

const TILE_CLASS = {
  current: 'fugassa-map-tile--current',
  visited: 'fugassa-map-tile--visited',
  intel: 'fugassa-map-tile--intel',
  fog: 'fugassa-map-tile--fog',
};

export async function mountMapScreen(root, { saveId, onClose, onStateChange }) {
  root.className = 'fugassa-screen fugassa-screen--map';
  root.innerHTML = `
    <header class="fugassa-screen-head">
      <h2>World Map</h2>
      <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-close>Back to game</button>
    </header>
    <div class="fugassa-screen-body">
      <div class="fugassa-map-legend">
        <span><span class="fugassa-map-swatch fugassa-map-tile--current"></span> here</span>
        <span><span class="fugassa-map-swatch fugassa-map-tile--visited"></span> visited</span>
        <span><span class="fugassa-map-swatch fugassa-map-tile--intel"></span> known</span>
        <span><span class="fugassa-map-swatch fugassa-map-tile--fog"></span> unknown</span>
        <span class="fugassa-muted">Click a cell to set travel coordinates.</span>
      </div>
      <div class="fugassa-map-wrap" data-grid><p class="fugassa-muted">Loading map…</p></div>
      <p class="fugassa-map-hover-info" data-hover-info>&nbsp;</p>
      <form class="fugassa-map-travel">
        <label>X <input type="number" name="x" class="fugassa-map-coord" /></label>
        <label>Y <input type="number" name="y" class="fugassa-map-coord" /></label>
        <label>Z <input type="number" name="z" value="0" class="fugassa-map-coord" /></label>
        <label>Mode <select name="mode"></select></label>
        <button type="submit" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm">Travel</button>
      </form>
      <p class="fugassa-muted" data-feedback></p>
    </div>
  `;

  const gridEl = root.querySelector('[data-grid]');
  const hoverInfo = root.querySelector('[data-hover-info]');
  const feedback = root.querySelector('[data-feedback]');
  const form = root.querySelector('.fugassa-map-travel');
  const modeSelect = form.querySelector('[name="mode"]');

  root.querySelector('[data-close]').addEventListener('click', () => onClose?.());

  let mapData = null;
  try {
    mapData = await api.getGameMap(saveId);
  } catch (error) {
    gridEl.innerHTML = `<p class="fugassa-muted">${escapeHtml(error.message)}</p>`;
    return;
  }

  (mapData.travel_modes || ['walk']).forEach((m) => {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    modeSelect.appendChild(opt);
  });

  const player = mapData.player || {};
  form.x.value = player.x ?? 0;
  form.y.value = player.y ?? 0;
  form.z.value = player.z ?? 0;

  const renderGrid = (cells) => {
    gridEl.innerHTML = '';
    const table = document.createElement('div');
    table.className = 'fugassa-map-grid';
    // Real CSS grid (grid-template-columns matching the row width) instead
    // of a flex-row-per-row stack — a single aligned grid, not a "pile of
    // squares" that can drift out of alignment.
    table.style.gridTemplateColumns = `repeat(${cells[0]?.length || 1}, 1fr)`;
    cells.forEach((row) => {
      row.forEach((cell) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `fugassa-map-tile ${TILE_CLASS[cell.state] || TILE_CLASS.fog}`;
        // Single-letter glyph only — full info (name/description) is
        // surfaced in the hover panel below, not crammed into the tile.
        btn.textContent = cell.biome || (cell.state === 'current' ? '@' : cell.state === 'visited' ? '·' : '?');
        btn.addEventListener('mouseenter', () => {
          hoverInfo.textContent = cell.tooltip || `(${cell.x}, ${cell.y}, ${cell.z})`;
        });
        btn.addEventListener('mouseleave', () => {
          hoverInfo.innerHTML = '&nbsp;';
        });
        btn.addEventListener('focus', () => {
          hoverInfo.textContent = cell.tooltip || `(${cell.x}, ${cell.y}, ${cell.z})`;
        });
        btn.addEventListener('click', () => {
          form.x.value = cell.x;
          form.y.value = cell.y;
          form.z.value = cell.z;
        });
        table.appendChild(btn);
      });
    });
    gridEl.appendChild(table);
  };

  renderGrid(mapData.cells || []);

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    feedback.textContent = 'Traveling…';
    try {
      const res = await api.travelGame(saveId, {
        x: Number(form.x.value),
        y: Number(form.y.value),
        z: Number(form.z.value),
        mode: modeSelect.value,
      });
      feedback.textContent = res.message || 'Travel complete.';
      onStateChange?.(res.state);
      const refreshed = await api.getGameMap(saveId);
      renderGrid(refreshed.cells || []);
      form.x.value = refreshed.player?.x ?? form.x.value;
      form.y.value = refreshed.player?.y ?? form.y.value;
      form.z.value = refreshed.player?.z ?? form.z.value;
    } catch (error) {
      feedback.textContent = error.message || String(error);
    }
  });
}
