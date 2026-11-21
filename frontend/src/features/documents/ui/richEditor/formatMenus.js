import { topPortalZ, attachColorPicker } from '../host.js';
import { _richSelectionCell, _richTableCanMergeRight, _richTableCanSplitCell, _focusRichTableCell } from '../richTables.js';
// frontend/src/features/documents/ui/richEditor/formatMenus.js
/**
 * Document Editor Rich-Text Subsystem: formatMenus
 */

import { activeDocId, docs } from '../../application/documentState.js';
import { _RICH_COLOR_PALETTE, _RICH_FONT_SIZE_PX } from '../../domain/richTextRules.js';
import { _currentBlockTag, _normalizedRichMenuColor, _richMenuColorHex, applyMdFormat } from './formatting.js';
import { _restoreRichLinkRange } from './links.js';
import { _emailRichbodyActive } from './richBodySync.js';
import { _richInlineCodeTypingArmed, _richSelectedSpacingBlockIndexes, _richSelectionChecklistItem, _richSelectionInlineCode, _richSpacingBlocks, _selectedRichImage, _setRichAlertType, _setRichCodeBlockLanguage } from './richOperations.js';

export let _mdDdActivationSerial = 0;

let _mdDdLastActivation = { kind: '', token: 0, time: 0 };

export function nextMdDropdownActivationToken() {
    return ++_mdDdActivationSerial;
  }

function _richSelectionNode(rich) {
  const selection = window.getSelection();
  if (!selection?.rangeCount) return null;
  const node = selection.getRangeAt(0).startContainer;
  const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
  return element && rich.contains(element) ? element : null;
}

function _richColorMenuItems(kind) {
    const prefix = kind === 'color' ? 'forecolor' : 'highlight';
    const reset = kind === 'color'
      ? [`${prefix}:default`, 'No color', 'transparent', 'reset']
      : [`${prefix}:transparent`, 'No highlight', 'transparent', 'reset'];
    return [
      reset,
      ..._RICH_COLOR_PALETTE.dark.map(([color, label]) => [`${prefix}:${color}`, label, color, 'dark']),
      ..._RICH_COLOR_PALETTE.light.map(([color, label]) => [`${prefix}:${color}`, label, color, 'light']),
    ];
  }

export function _richDropdownCurrentActions(kind, rich, selectedImage, selectedCell) {
    const current = new Set();
    if (!rich) return current;
    const block = _currentBlockTag(rich);
    try {
      if (kind === 'heading') {
        current.add(/^h[1-6]$/.test(block) ? block : 'paragraph');
      } else if (kind === 'code') {
        const pre = _richSelectionNode(rich)?.closest?.('pre');
        if (block === 'pre') {
          current.add('codeblock');
          const code = pre?.querySelector('code');
          const lang = String(code?.dataset?.lang || '').toLowerCase();
          const languageKeys = { js: 'javascript', javascript: 'javascript', py: 'python', python: 'python', bash: 'shell', sh: 'shell', json: 'json' };
          if (!lang) current.add('code-lang:plain');
          else if (languageKeys[lang]) current.add(`code-lang:${languageKeys[lang]}`);
        }
        if (_richInlineCodeTypingArmed || _richSelectionInlineCode(rich)) current.add('code');
      } else if (kind === 'alert') {
        const selection = window.getSelection();
        const node = selection?.anchorNode;
        const element = node?.nodeType === Node.TEXT_NODE ? node.parentElement : node;
        const type = element?.closest?.('blockquote.markdown-alert')?.dataset?.alert;
        if (type) {
          current.add('alert:selected');
          current.add(`alert:${String(type).toUpperCase()}`);
        }
      } else if (kind === 'list') {
        if (_richSelectionChecklistItem(rich)) current.add('check');
        else if (document.queryCommandState('insertOrderedList')) current.add('ol');
        else if (document.queryCommandState('insertUnorderedList')) current.add('ul');
      } else if (kind === 'font') {
        const value = String(document.queryCommandValue('fontName') || '').replace(/["']/g, '').toLowerCase();
        for (const name of ['Arial', 'Georgia', 'Times New Roman', 'Verdana', 'Courier New']) {
          if (value.includes(name.toLowerCase())) current.add(`fontname:${name}`);
        }
      } else if (kind === 'textsize') {
        const value = String(document.queryCommandValue('fontSize') || '3');
        current.add(`fontsize:${Object.hasOwn(_RICH_FONT_SIZE_PX, value) ? value : '3'}`);
      } else if (kind === 'align') {
        const selection = window.getSelection();
        const node = selection?.anchorNode;
        const element = node?.nodeType === Node.TEXT_NODE ? node.parentElement : node;
        const selectedBlock = element?.closest?.('p,div,li,h1,h2,h3,h4,h5,h6,blockquote,pre') || rich;
        const alignment = String(selectedBlock?.style?.textAlign || getComputedStyle(selectedBlock).textAlign || '').toLowerCase();
        if (alignment === 'center') current.add('aligncenter');
        else if (alignment === 'right' || alignment === 'end') current.add('alignright');
        else if (alignment === 'justify') current.add('alignjustify');
        else current.add('alignleft');
      } else if (kind === 'spacing') {
        const blocks = _richSpacingBlocks(rich);
        const indexes = _richSelectedSpacingBlockIndexes(rich);
        const values = new Set(indexes.map(index => blocks[index]?.style.lineHeight || 'normal'));
        if (values.size === 1) current.add(`linespacing:${values.values().next().value}`);
      } else if (kind === 'color' || kind === 'highlight') {
        const command = kind === 'color' ? 'foreColor' : 'hiliteColor';
        const value = _normalizedRichMenuColor(document.queryCommandValue(command));
        const themeFg = _normalizedRichMenuColor(getComputedStyle(rich).color);
        const choices = Object.values(_RICH_COLOR_PALETTE).flat().map(([choice]) => choice);
        const noHighlight = kind === 'highlight' && (!value || value === 'transparent' || value === 'rgba(0,0,0,0)');
        if (noHighlight) current.add('highlight:transparent');
        else {
          const isDefaultColor = kind === 'color' && (!value || (themeFg && value === themeFg));
          if (isDefaultColor) current.add('forecolor:default');
          else {
            const match = choices.find(choice => _normalizedRichMenuColor(choice) === value);
            if (match) current.add(`${kind === 'color' ? 'forecolor' : 'highlight'}:${match}`);
          }
        }
      } else if (kind === 'image' && selectedImage) {
        const sizeClass = Array.from(selectedImage.classList).find(name => name.startsWith('richtext-image-size-'));
        const alignClass = Array.from(selectedImage.classList).find(name => name.startsWith('richtext-image-align-'));
        current.add(sizeClass ? `image:size:${sizeClass.slice('richtext-image-size-'.length)}` : 'image:size:auto');
        current.add(`image:align:${alignClass ? alignClass.slice('richtext-image-align-'.length) : 'left'}`);
      } else if (kind === 'table' && selectedCell) {
        const table = selectedCell.closest('table');
        const cellAlignment = selectedCell.style.verticalAlign || 'top';
        current.add(`table:cell-align:${['middle', 'bottom'].includes(cellAlignment) ? cellAlignment : 'top'}`);
        const columnAlignments = Array.from(table.rows).map(row => row.cells[selectedCell.cellIndex]?.style.textAlign || '');
        if (columnAlignments.length && columnAlignments.every(value => value === columnAlignments[0])) {
          const alignment = columnAlignments[0];
          current.add(`table:column-align:${['left', 'center', 'right'].includes(alignment) ? alignment : 'reset'}`);
        }
        const firstRow = table?.rows?.[0];
        if (firstRow && Array.from(firstRow.cells).every(item => item.tagName === 'TH')) {
          current.add('table:toggle-header-row');
        }
        if (table?.rows?.length && Array.from(table.rows).every(row => row.cells[0]?.tagName === 'TH')) {
          current.add('table:toggle-header-column');
        }
      }
    } catch (_) {}
    return current;
  }

export function _showMdDropdown(toggleBtn, focusIndex = null, activationToken = null) {
    const kind = toggleBtn.dataset.dd;
    const now = Date.now();
    const token = activationToken || toggleBtn._mdDdActivationToken || nextMdDropdownActivationToken();
    toggleBtn._mdDdActivationToken = token;
    const duplicateActivation = _mdDdLastActivation.kind === kind
      && _mdDdLastActivation.token === token
      && (now - _mdDdLastActivation.time) < 400;
    if (duplicateActivation) return;
    _mdDdLastActivation = { kind, token, time: now };
    const existing = document.getElementById('doc-md-dd-menu');
    const prevKind = existing && existing.dataset.dd;
    if (existing) {
      if (existing._dismiss) existing._dismiss(false);
      else existing.remove();
    }
    if (existing && prevKind === kind) return; // same toggle clicked → just close

    const groups = {
      heading: [
        ['paragraph', 'Paragraph', 'P'],
        ['h1', 'Heading 1', 'H1'],
        ['h2', 'Heading 2', 'H2'],
        ['h3', 'Heading 3', 'H3'],
        ['h4', 'Heading 4', 'H4'],
        ['h5', 'Heading 5', 'H5'],
        ['h6', 'Heading 6', 'H6'],
      ],
      font: [
        ['fontname:Arial', 'Arial', 'Aa'],
        ['fontname:Georgia', 'Georgia', 'Gg'],
        ['fontname:Times New Roman', 'Times New Roman', 'Tt'],
        ['fontname:Verdana', 'Verdana', 'Vv'],
        ['fontname:Courier New', 'Courier New', 'Cc'],
      ],
      code: [
        ['code', 'Inline code', '`'], ['codeblock', 'Code block', '```'],
        ['code-lang:plain', 'Language: Plain text', 'T'],
        ['code-lang:javascript', 'Language: JavaScript', 'JS'],
        ['code-lang:python', 'Language: Python', 'Py'],
        ['code-lang:shell', 'Language: Shell', '$'],
        ['code-lang:json', 'Language: JSON', '{}'],
      ],
      alert: [
        ['alert:NOTE', 'Note', 'N'], ['alert:TIP', 'Tip', 'T'], ['alert:IMPORTANT', 'Important', '!'],
        ['alert:WARNING', 'Warning', 'W'], ['alert:CAUTION', 'Caution', '×'],
      ],
      list: [
        ['ul', 'Bullet list', '•'],
        ['ol', 'Numbered list', '1.'],
        ['check', 'Checklist', '☑'],
      ],
      textsize: [
        ['fontsize:8px', '8 px', '8'],
        ['fontsize:10px', '10 px', '10'],
        ['fontsize:12px', '12 px', '12'],
        ['fontsize:16px', '16 px', '16'],
      ],
      align: [
        ['alignleft', 'Align left', '≡'],
        ['aligncenter', 'Align center', '≡'],
        ['alignright', 'Align right', '≡'],
        ['alignjustify', 'Justify', '☰'],
      ],
      spacing: [
        ['linespacing:normal', 'Normal', 'Auto'],
        ['linespacing:1', 'Single', '1.0'],
        ['linespacing:1.15', 'Comfortable', '1.15'],
        ['linespacing:1.5', 'One and a half', '1.5'],
        ['linespacing:2', 'Double', '2.0'],
      ],
      table: [
        ['table:insert:2:2', 'Insert 2 × 2', '2×2'],
        ['table:insert:3:3', 'Insert 3 × 3', '3×3'],
        ['table:insert:4:4', 'Insert 4 × 4', '4×4'],
        ['table:toggle-header-row', 'Header row', 'H↔'],
        ['table:toggle-header-column', 'Header column', 'H↕'],
        ['table:merge-right', 'Merge with right', '↔'],
        ['table:split-cell', 'Split cell', '÷'],
        ['table:cell-align:top', 'Align cell top', '↑'],
        ['table:cell-align:middle', 'Align cell middle', '↕'],
        ['table:cell-align:bottom', 'Align cell bottom', '↓'],
        ['table:column-align:left', 'Column: align left', '←'],
        ['table:column-align:center', 'Column: align center', '↔'],
        ['table:column-align:right', 'Column: align right', '→'],
        ['table:column-align:reset', 'Column: clear alignment', '↺'],
        ['table:row-above', 'Add row above', '↑'],
        ['table:row-below', 'Add row below', '↓'],
        ['table:column-left', 'Add column left', '←'],
        ['table:column-right', 'Add column right', '→'],
        ['table:delete-row', 'Delete row', '−'],
        ['table:delete-column', 'Delete column', '−'],
        ['table:delete', 'Delete table', '×'],
      ],
      image: [
        ['image:size:auto', 'Original size', 'Auto'],
        ['image:size:100', 'Full width', '100%'],
        ['image:size:60', 'Medium', '60%'],
        ['image:size:35', 'Small', '35%'],
        ['image:align:left', 'Align left', '←'],
        ['image:align:center', 'Align center', '↔'],
        ['image:align:right', 'Align right', '→'],
        ['image:caption', 'Edit caption', 'T'],
        ['image:alt', 'Edit description', 'Aa'],
        ['image:delete', 'Remove image', '×'],
      ],
      color: _richColorMenuItems('color'),
      highlight: _richColorMenuItems('highlight'),
      separator: [
        ['separator:solid', 'Solid line', '—'],
        ['separator:dashed', 'Dashed line', '╌'],
        ['separator:dotted', 'Dotted line', '···'],
        ['separator:double', 'Double line', '═'],
      ],
    };
    let items = groups[kind];
    const activeDoc = activeDocId && docs.get(activeDocId);
    if (kind === 'list' && activeDoc?.language === 'email') {
      items = items?.filter(([action]) => action !== 'check');
    }
    if (kind === 'separator' && String(activeDoc?.language || '').toLowerCase() === 'markdown') {
      items = items?.filter(([action]) => action === 'separator:solid');
    }
    if (kind === 'table') {
      const isMd = String(activeDoc?.language || '').toLowerCase() === 'markdown';
      if (isMd) {
        items = items?.filter(([action]) => (
          action.startsWith('table:insert:')
          || action === 'table:row-above'
          || action === 'table:row-below'
          || action === 'table:column-left'
          || action === 'table:column-right'
          || action === 'table:delete-row'
          || action === 'table:delete-column'
          || action.startsWith('table:column-align:')
          || action === 'table:delete'
        ));
      } else {
        items = items?.filter(([action]) => !action.startsWith('table:column-align:'));
      }
    }
    if (!items) return;

    const rect = toggleBtn.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.id = 'doc-md-dd-menu';
    menu.dataset.dd = kind;
    menu.className = 'doc-overflow-menu open';
    menu.setAttribute('role', 'menu');
    const toggleId = toggleBtn.id || `doc-md-dd-toggle-${kind}`;
    toggleBtn.id = toggleId;
    toggleBtn.setAttribute('aria-haspopup', 'menu');
    toggleBtn.setAttribute('aria-controls', menu.id);
    toggleBtn.setAttribute('aria-expanded', 'true');
    menu.setAttribute('aria-labelledby', toggleId);
    if (kind === 'color' || kind === 'highlight') menu.classList.add('rich-color-palette-menu');
    menu.style.position = 'fixed';
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.left = rect.left + 'px';
    menu.style.zIndex = String(topPortalZ());
    const rich = _emailRichbodyActive();
    if (kind === 'code' && (!rich || String(activeDoc?.language || '').toLowerCase() !== 'markdown')) items = items?.filter(([action]) => !action.startsWith('code-lang:'));
    const selectedCell = rich ? _richSelectionCell(rich) : null;
    const selectedImage = rich ? _selectedRichImage(rich) : null;
    const currentActions = _richDropdownCurrentActions(kind, rich, selectedImage, selectedCell);
    const statefulKinds = new Set(['heading', 'code', 'list', 'font', 'textsize', 'align', 'spacing', 'color', 'highlight', 'image', 'alert']);
    const checkboxKinds = new Set(['code', 'image']);
    let savedRichRange = null;
    const selection = window.getSelection();
    if (rich && selection?.rangeCount) {
      const range = selection.getRangeAt(0);
      if (rich.contains(range.commonAncestorContainer)) savedRichRange = range.cloneRange();
    }
    const paletteGroups = {};
    const ensurePaletteGroup = tone => {
      if (paletteGroups[tone]) return paletteGroups[tone];
      let grid = menu.querySelector('.rich-color-palette-grid');
      if (!grid) {
        grid = document.createElement('div');
        grid.className = 'rich-color-palette-grid';
        grid.setAttribute('role', 'none');
        menu.appendChild(grid);
      }
      const group = document.createElement('div');
      group.className = 'rich-color-palette-group';
      group.setAttribute('role', 'group');
      group.setAttribute('aria-label', tone === 'dark' ? 'Dark colors' : 'Light colors');
      const heading = document.createElement('div');
      heading.className = 'rich-color-palette-label';
      heading.textContent = tone === 'dark' ? 'Dark' : 'Light';
      group.appendChild(heading);
      grid.appendChild(group);
      paletteGroups[tone] = group;
      return group;
    };
    items.forEach(([md, label, ico, tone]) => {
      const it = document.createElement('button');
      it.type = 'button';
      it.className = 'doc-overflow-item';
      const tableHeaderToggle = md === 'table:toggle-header-row' || md === 'table:toggle-header-column';
      const tableCellAlignment = md.startsWith('table:cell-align:');
      const columnAlignment = md.startsWith('table:column-align:');
      const statefulItem = tableHeaderToggle || tableCellAlignment || columnAlignment || (statefulKinds.has(kind) && !md.endsWith(':alt') && !md.endsWith(':delete'));
      it.setAttribute('role', statefulItem ? ((checkboxKinds.has(kind) || tableHeaderToggle) ? 'menuitemcheckbox' : 'menuitemradio') : 'menuitem');
      it.tabIndex = -1;
      if (statefulItem) it.setAttribute('aria-checked', currentActions.has(md) ? 'true' : 'false');
      if (currentActions.has(md)) it.classList.add('is-current');
      if (md.startsWith('table:delete') || md === 'image:delete') it.classList.add('rich-table-danger');
      const needsTableSelection = md.startsWith('table:') && !md.startsWith('table:insert:');
      const needsImageSelection = md.startsWith('image:');
      const unavailableTableAction = selectedCell && (
        (md === 'table:merge-right' && !_richTableCanMergeRight(selectedCell))
        || (md === 'table:split-cell' && !_richTableCanSplitCell(selectedCell))
      );
      if ((needsTableSelection && (!selectedCell || unavailableTableAction)) || (needsImageSelection && !selectedImage)
          || (md.startsWith('alert:') && !currentActions.has('alert:selected'))
          || (md.startsWith('code-lang:') && !currentActions.has('codeblock'))) {
        it.disabled = true;
        it.setAttribute('aria-disabled', 'true');
      }
      const icoSpan = document.createElement('span');
      icoSpan.className = 'md-dd-ico';
      if (kind === 'color' || kind === 'highlight') {
        icoSpan.classList.add('rich-color-swatch');
        if (kind === 'highlight') icoSpan.classList.add('rich-highlight-swatch');
        if (ico === 'transparent') icoSpan.classList.add('is-transparent');
        else icoSpan.style.background = ico;
      } else {
        icoSpan.textContent = ico;
        if (kind === 'align') icoSpan.classList.add(`rich-align-${md.replace('align', '')}`);
        if (kind === 'font') icoSpan.style.fontFamily = md.slice('fontname:'.length);
      }
      const lbl = document.createElement('span');
      lbl.textContent = label;
      if (kind === 'align') {
        const previewAlign = {
          alignleft: 'left',
          aligncenter: 'center',
          alignright: 'right',
          alignjustify: 'justify',
        }[md];
        lbl.classList.add('rich-align-preview');
        lbl.style.textAlign = previewAlign || 'left';
        lbl.style.flex = '1 1 120px';
        lbl.style.minWidth = '120px';
      }
      if (kind === 'heading') {
        const level = md === 'paragraph' ? 0 : Number(md.slice(1));
        const scale = level ? [1.35, 1.22, 1.12, 1.04, 0.97, 0.91][level - 1] : 0.9;
        lbl.style.fontSize = `${scale}em`;
        lbl.style.lineHeight = '1.15';
        if (level > 0) lbl.style.fontWeight = level <= 2 ? '600' : '500';
      }
      if (kind === 'font') lbl.style.fontFamily = md.slice('fontname:'.length);
      it.append(icoSpan, lbl);
      if (statefulItem) {
        const currentMark = document.createElement('span');
        currentMark.className = 'md-dd-current';
        currentMark.setAttribute('aria-hidden', 'true');
        currentMark.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        it.appendChild(currentMark);
      }
      // Don't let the menu item steal focus from the editor (preserve selection).
      it.addEventListener('mousedown', (ev) => ev.preventDefault());
      it.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (it.disabled) return;
        menu._dismiss?.(false);
        const applyDropdownAction = () => {
          if (md.startsWith('table:') && rich && selectedCell?.isConnected) {
            _focusRichTableCell(rich, selectedCell);
          } else if (rich && savedRichRange) {
            _restoreRichLinkRange(rich, savedRichRange);
          }
          if (md.startsWith('alert:')) _setRichAlertType(rich, md.slice('alert:'.length));
          else if (md.startsWith('code-lang:')) _setRichCodeBlockLanguage(rich, md.slice('code-lang:'.length));
          else applyMdFormat(md);
        };
        if (md.startsWith('table:')) requestAnimationFrame(applyDropdownAction);
        else applyDropdownAction();
      });
      if (tone === 'dark' || tone === 'light') ensurePaletteGroup(tone).appendChild(it);
      else {
        if (tone === 'reset') it.classList.add('rich-color-reset');
        menu.appendChild(it);
      }
    });
    if (kind === 'textsize') {
      let sizeValue = '3';
      try { sizeValue = String(document.queryCommandValue('fontSize') || '3'); } catch (_) {}
      if (!Object.hasOwn(_RICH_FONT_SIZE_PX, sizeValue)) sizeValue = '3';

      const sliderRow = document.createElement('div');
      sliderRow.className = 'rich-toolbar-slider-row';
      sliderRow.setAttribute('role', 'none');
      const sliderLabel = document.createElement('span');
      sliderLabel.textContent = 'Font size';
      const slider = document.createElement('input');
      slider.type = 'range';
      slider.className = 'rich-toolbar-range';
      slider.min = '1';
      slider.max = '7';
      slider.step = '1';
      slider.value = sizeValue;
      slider.setAttribute('aria-label', 'Font size');
      slider.setAttribute('aria-valuetext', `${_RICH_FONT_SIZE_PX[sizeValue]} pixels`);
      let sliderPointerActive = false;
      const valueLabel = document.createElement('input');
      valueLabel.type = 'number';
      valueLabel.className = 'rich-toolbar-slider-value';
      valueLabel.value = String(_RICH_FONT_SIZE_PX[sizeValue]);
      valueLabel.min = '8';
      valueLabel.max = '96';
      valueLabel.step = '1';
      valueLabel.inputMode = 'numeric';
      valueLabel.setAttribute('aria-label', 'Font size in pixels');
      valueLabel.title = 'Type a font size in pixels';
      const applySliderSize = () => {
        const pixels = _RICH_FONT_SIZE_PX[slider.value] || 16;
        valueLabel.value = String(pixels);
        slider.setAttribute('aria-valuetext', `${pixels} pixels`);
        if (rich && savedRichRange) _restoreRichLinkRange(rich, savedRichRange);
        applyMdFormat(`fontsize:${slider.value}`);
        const liveSelection = window.getSelection();
        if (rich && liveSelection?.rangeCount && rich.contains(liveSelection.getRangeAt(0).commonAncestorContainer)) {
          savedRichRange = liveSelection.getRangeAt(0).cloneRange();
        }
      };
      slider.addEventListener('pointerdown', () => {
        sliderPointerActive = true;
      });
      slider.addEventListener('pointerup', () => {
        sliderPointerActive = false;
      });
      slider.addEventListener('pointercancel', () => {
        sliderPointerActive = false;
      });
      slider.addEventListener('input', () => {
        const pixels = _RICH_FONT_SIZE_PX[slider.value] || 16;
        valueLabel.value = String(pixels);
        slider.setAttribute('aria-valuetext', `${pixels} pixels`);
        // Applying the command focuses the contenteditable. During a pointer
        // drag that steals focus/capture from the range after its first step,
        // making it behave like a click-only control. Keep the thumb live
        // while dragging and commit once the native change event fires.
        if (!sliderPointerActive) applySliderSize();
      });
      slider.addEventListener('change', applySliderSize);
      const applyTypedSize = () => {
        const pixels = Math.max(8, Math.min(96, Math.round(Number(valueLabel.value) || 16)));
        valueLabel.value = String(pixels);
        const closest = Object.entries(_RICH_FONT_SIZE_PX)
          .sort(([, a], [, b]) => Math.abs(a - pixels) - Math.abs(b - pixels))[0]?.[0] || '3';
        slider.value = closest;
        if (rich && savedRichRange) _restoreRichLinkRange(rich, savedRichRange);
        applyMdFormat(`fontsize:${pixels}px`);
        const liveSelection = window.getSelection();
        if (rich && liveSelection?.rangeCount && rich.contains(liveSelection.getRangeAt(0).commonAncestorContainer)) {
          savedRichRange = liveSelection.getRangeAt(0).cloneRange();
        }
      };
      valueLabel.addEventListener('change', applyTypedSize);
      valueLabel.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
          event.preventDefault();
          applyTypedSize();
          valueLabel.blur();
        }
      });
      sliderRow.append(sliderLabel, slider, valueLabel);
      menu.appendChild(sliderRow);
    }
    if (kind === 'color' || kind === 'highlight') {
      const command = kind === 'color' ? 'foreColor' : 'hiliteColor';
      const fallback = kind === 'color'
        ? _richMenuColorHex(getComputedStyle(rich).color, '#1f2937')
        : '#fef08a';
      let currentColor = fallback;
      try { currentColor = _richMenuColorHex(document.queryCommandValue(command), fallback); } catch (_) {}

      const customRow = document.createElement('div');
      customRow.className = 'rich-toolbar-color-custom';
      customRow.setAttribute('role', 'none');
      const customLabel = document.createElement('span');
      customLabel.textContent = 'Custom';
      const customInput = document.createElement('input');
      customInput.type = 'color';
      customInput.className = 'rich-toolbar-color-input';
      customInput.value = currentColor;
      customInput.title = kind === 'color' ? 'Custom text color' : 'Custom highlight color';
      customInput.setAttribute('aria-label', customInput.title);
      customInput.addEventListener('input', () => {
        if (rich && savedRichRange) _restoreRichLinkRange(rich, savedRichRange);
        applyMdFormat(`${kind === 'color' ? 'forecolor' : 'highlight'}:${customInput.value}`);
      });
      customRow.append(customLabel, customInput);
      menu.appendChild(customRow);
      attachColorPicker(customInput);
    }
    document.body.appendChild(menu);

    // Keep long menus usable above small mobile keyboards and prevent a
    // right-edge toolbar button from opening a menu outside the viewport.
    const viewportWidth = window.visualViewport?.width || window.innerWidth;
    const viewportHeight = window.visualViewport?.height || window.innerHeight;
    menu.style.maxHeight = `${Math.max(120, viewportHeight - 16)}px`;
    menu.style.overflowY = 'auto';
    const menuRect = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(rect.left, viewportWidth - menuRect.width - 8))}px`;
    if (rect.bottom + 4 + menuRect.height > viewportHeight - 8) {
      menu.style.top = `${Math.max(8, rect.top - menuRect.height - 4)}px`;
    }

    const dismiss = (restoreFocus = false) => {
      if (!menu.isConnected) return;
      menu.remove();
      toggleBtn.setAttribute('aria-expanded', 'false');
      toggleBtn.removeAttribute('aria-controls');
      document.removeEventListener('click', close, true);
      document.removeEventListener('keydown', close, true);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close, true);
      window.visualViewport?.removeEventListener('resize', close, true);
      if (restoreFocus) toggleBtn.focus();
    };
    menu._dismiss = dismiss;
    const enabledItems = () => Array.from(menu.querySelectorAll('.doc-overflow-item:not(:disabled), .rich-toolbar-range'));
    menu.addEventListener('keydown', (ev) => {
      if (ev.target.matches?.('.rich-toolbar-range')
          && ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(ev.key)) return;
      const entries = enabledItems();
      if (!entries.length) return;
      const current = entries.indexOf(document.activeElement);
      let next = null;
      if (ev.key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % entries.length;
      else if (ev.key === 'ArrowUp') next = current < 0 ? entries.length - 1 : (current - 1 + entries.length) % entries.length;
      else if (ev.key === 'Home') next = 0;
      else if (ev.key === 'End') next = entries.length - 1;
      else if (ev.key === 'Escape') {
        ev.preventDefault();
        ev.stopPropagation();
        dismiss(true);
        return;
      } else if (ev.key === 'Tab') {
        dismiss(false);
        return;
      }
      if (next !== null) {
        ev.preventDefault();
        entries[next].focus();
      }
    });

    const close = (ev) => {
      if (ev?.type === 'scroll') {
        const scroller = ev.target;
        // Autosave can scroll the document's tab strip without moving this
        // menu anchor. Only scroll affecting the anchor can invalidate it.
        if (scroller !== window && scroller !== document && !scroller?.contains?.(toggleBtn)) return;
        if (scroller?.closest?.('.md-toolbar-items')) return;
      }
      if (ev && ev.type === 'keydown') {
        if (ev.key !== 'Escape') return;
        // Let the menu's own key handler restore focus to its toggle.
        if (menu.contains(ev.target)) return;
        ev.preventDefault();
        ev.stopPropagation();
        ev.stopImmediatePropagation?.();
      }
      if (ev && ev.type === 'click') {
        if (ev.target?.closest?.('.cp-popover')) return;
        if (menu.contains(ev.target) || toggleBtn.contains(ev.target)) return;
      }
      dismiss(ev?.type === 'keydown');
    };
    setTimeout(() => {
      if (!menu.isConnected) return;
      document.addEventListener('click', close, true);
      document.addEventListener('keydown', close, true);
      window.addEventListener('scroll', close, true);
      window.addEventListener('resize', close, true);
      window.visualViewport?.addEventListener('resize', close, true);
    }, 0);
    if (focusIndex !== null) {
      const entries = enabledItems();
      const index = focusIndex < 0 ? entries.length - 1 : Math.min(focusIndex, entries.length - 1);
      requestAnimationFrame(() => entries[index]?.focus());
    }
  }
