import { _syncHeaderActions } from '../../application/documentCommands.js';
import { _isFormBackedDoc, _setPdfViewActive } from '../pdfTools.js';
import { _setMarkdownPreviewActive, _setDocxPreviewActive, exitHtmlPreview } from '../previewModes.js';
// ui/header/languagePicker.js

import { activeDocId, docs } from '../../application/documentState.js';

import { _scheduleAutoSave } from '../../application/persistence.js';

import { _isDocxLang, _isRenderLang, _isRichTextLang } from '../../domain/documentKinds.js';

import { _parseEmailHeader } from '../../domain/emailMessage.js';

import { updateLanguage } from '../documentActions.js';

import { syncHighlighting } from '../editor/sourceEditor.js';

import { _hideEmailFields, _showEmailFields } from '../email/composerFields.js';

import { _emailRichbodyActive, _syncEmailRichbody, _syncRichEmptyImport } from '../richEditor/richBodySync.js';

import { _showRichTextEditor, _showMarkdownVisualEditor, _hideMarkdownVisualEditor } from '../richEditor/richBodyWiring.js';
import {
  hideMarkdownCompatBanner,
  isMarkdownVisualModeActive,
  isVisualMarkdownDoc,
  renderMarkdownCompatBanner,
  resolveMarkdownEditorMode,
  syncVisualMarkdownToDoc,
} from '../richEditor/markdownVisualSync.js';

import { _syncEditorPlaceholder } from '../status/documentStatus.js';

import { langIcon, topPortalZ, uiModule } from '../host.js';

export function bindLanguagePicker({ pane, paneLifetime }) {
const _syncLangIcon = () => {
      const iconEl = document.getElementById('doc-language-icon');
      const v = document.getElementById('doc-language-select')?.value || '';
      if (iconEl) iconEl.innerHTML = v ? langIcon(v, 14, { style: 'opacity:0.75;' }) : '';
      _syncEditorPlaceholder();
    };
(function _interceptLangSelectValue() {
      const ls = document.getElementById('doc-language-select');
      if (!ls) return;
      const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
      if (!desc || !desc.set) return;
      Object.defineProperty(ls, 'value', {
        configurable: true,
        get() { return desc.get.call(this); },
        set(v) { desc.set.call(this, v); _syncLangIcon(); _syncLangPicker(); },
      });
      _syncLangIcon();  // initial paint
    })();
let _syncLangPicker = () => {};
(function _initLangPicker() {
      const ls = document.getElementById('doc-language-select');
      if (!ls || ls.dataset.pickerWired === '1') return;
      ls.dataset.pickerWired = '1';

      const trigger = document.createElement('button');
      trigger.type = 'button';
      trigger.id = 'doc-langpicker-trigger';
      trigger.className = 'doc-langpicker-trigger';
      trigger.setAttribute('aria-haspopup', 'listbox');
      trigger.setAttribute('aria-expanded', 'false');

      const menu = document.createElement('div');
      menu.id = 'doc-langpicker-menu';
      menu.className = 'doc-langpicker-menu';
      menu.setAttribute('role', 'listbox');
      menu.style.display = 'none';

      // Build the menu rows from the <select>'s real <option>s — single
      // source of truth, future additions to the select auto-propagate.
      const _buildMenu = () => {
        menu.innerHTML = '';
        const options = Array.from(ls.options);
        const byValue = new Map(options.map(opt => [opt.value, opt]));
        const groups = [
          { label: 'Documents', values: ['richtext', 'markdown', 'pdf'] },
          { label: 'Email & data', values: ['email', 'csv'] },
          {
            label: 'Code',
            values: options
              .map(opt => opt.value)
              .filter(value => !['richtext', 'markdown', 'pdf', 'email', 'csv'].includes(value))
              .sort((a, b) => a.localeCompare(b)),
          },
        ];
        groups.forEach((group, groupIndex) => {
          const groupOptions = group.values.map(value => byValue.get(value)).filter(Boolean);
          if (!groupOptions.length) return;
          if (groupIndex) {
            const divider = document.createElement('div');
            divider.className = 'doc-langpicker-divider';
            divider.setAttribute('role', 'separator');
            menu.appendChild(divider);
          }
          const heading = document.createElement('div');
          heading.className = 'doc-langpicker-group';
          heading.textContent = group.label;
          heading.setAttribute('aria-hidden', 'true');
          menu.appendChild(heading);
          groupOptions.forEach(opt => {
          const row = document.createElement('button');
          row.type = 'button';
          row.className = 'doc-langpicker-item';
          row.dataset.value = opt.value;
          row.setAttribute('role', 'option');
          const ic = opt.value
            ? langIcon(opt.value, 14, { style: 'opacity:0.85;' })
            // Empty value = the "type" placeholder option — small dot so the
            // row still aligns with the others (and the picker shows _some_
            // mark when no type is set yet).
            : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="opacity:0.5;"><circle cx="12" cy="12" r="3"/></svg>';
          row.innerHTML = ic +
                          `<span class="doc-langpicker-label">${uiModule.esc(opt.textContent || opt.value)}</span>`;
          row.addEventListener('click', (e) => {
            e.stopPropagation();
            if (ls.value !== opt.value) {
              ls.value = opt.value;
              ls.dispatchEvent(new Event('change', { bubbles: true }));
            }
            _close();
          });
          menu.appendChild(row);
          });
        });
      };
      _buildMenu();

      _syncLangPicker = () => {
        const v = ls.value || '';
        const sel = Array.from(ls.options).find(o => o.value === v) || ls.options[0];
        const ic = v
          ? langIcon(v, 14, { style: 'opacity:0.85;flex-shrink:0;' })
          // No language picked yet → small dot mark so the trigger isn't bare.
          : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="opacity:0.5;flex-shrink:0;"><circle cx="12" cy="12" r="3"/></svg>';
        trigger.innerHTML = ic +
          `<span class="doc-langpicker-label">${uiModule.esc(sel?.textContent || 'type')}</span>` +
          '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:4px;opacity:0.6;"><polyline points="6 9 12 15 18 9"/></svg>';
        // Highlight the current row in the open menu.
        menu.querySelectorAll('.doc-langpicker-item').forEach(r => {
          r.classList.toggle('is-selected', r.dataset.value === v);
        });
      };

      const _close = () => {
        menu.style.display = 'none';
        trigger.setAttribute('aria-expanded', 'false');
        document.removeEventListener('click', _outsideClick, true);
        document.removeEventListener('keydown', _escKey, true);
      };
      const _outsideClick = (e) => {
        if (!menu.contains(e.target) && !trigger.contains(e.target)) _close();
      };
      const _escKey = (e) => {
        if (e.key !== 'Escape' || menu.style.display === 'none') return;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _close();
      };

      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = menu.style.display !== 'none';
        if (open) { _close(); return; }
        // Position the menu under the trigger. It is body-mounted + fixed so
        // it escapes the document/footer stacking context, then promoted above
        // whatever tool window is currently on top.
        const r = trigger.getBoundingClientRect();
        menu.style.display = 'block';
        menu.style.visibility = 'hidden';
        menu.style.position = 'fixed';
        menu.style.zIndex = String(topPortalZ());
        menu.style.minWidth = r.width + 'px';
        menu.style.maxWidth = 'calc(100vw - 16px)';
        menu.style.maxHeight = 'min(60vh, calc(100dvh - 16px))';
        const left = Math.min(Math.max(8, r.left), Math.max(8, window.innerWidth - 168));
        menu.style.left = left + 'px';
        menu.style.top = Math.min(window.innerHeight - 8, r.bottom + 4) + 'px';
        // If it would overflow the bottom of the viewport, flip above.
        requestAnimationFrame(() => {
          const mr = menu.getBoundingClientRect();
          const maxLeft = Math.max(8, window.innerWidth - mr.width - 8);
          menu.style.left = Math.min(Math.max(8, r.left), maxLeft) + 'px';
          if (mr.bottom > window.innerHeight - 8) {
            menu.style.top = Math.max(8, r.top - mr.height - 4) + 'px';
          }
          menu.style.visibility = 'visible';
        });
        trigger.setAttribute('aria-expanded', 'true');
        document.addEventListener('click', _outsideClick, true);
        document.addEventListener('keydown', _escKey, true);
      });

      // Hide the native select but keep it in the layout for screen readers
      // / programmatic value sets / focus management. The icon span next to
      // it is removed since the trigger now carries the current icon.
      ls.classList.add('doc-langpicker-native-hidden');
      const iconSpan = document.getElementById('doc-language-icon');
      if (iconSpan) iconSpan.remove();
      ls.parentNode.insertBefore(trigger, ls);
      // Menu is body-mounted so position:fixed coords work cleanly.
      document.body.appendChild(menu);

      _syncLangPicker();
    })();
document.getElementById('doc-language-select').addEventListener('change', () => {
      // Run output belongs to the previous language. Clear it immediately so
      // an old empty/error result cannot remain attached to the new format.
      const staleRunOutput = document.getElementById('doc-run-output');
      if (staleRunOutput) {
        staleRunOutput.style.display = 'none';
        staleRunOutput.innerHTML = '';
      }
      const changingDoc = activeDocId && docs.get(activeDocId);
      const previousLanguage = changingDoc?.language || '';
      if (_isRichTextLang(previousLanguage)) {
        const rich = _emailRichbodyActive();
        if (rich) _syncEmailRichbody(rich);
      } else if (changingDoc && isMarkdownVisualModeActive()) {
        const rich = document.getElementById('doc-email-richbody');
        syncVisualMarkdownToDoc(rich, { doc: changingDoc });
        if (changingDoc._visualSyncBlocked) {
          document.getElementById('doc-language-select').value = previousLanguage;
          _syncLangPicker();
          return;
        }
        _hideMarkdownVisualEditor();
        hideMarkdownCompatBanner();
      } else if (changingDoc && previousLanguage === 'email') {
        const rich = _emailRichbodyActive();
        const fields = _parseEmailHeader(changingDoc.content || '');
        changingDoc.content = rich ? rich.innerHTML : (fields.body || '');
      } else if (changingDoc && previousLanguage !== 'email') {
        changingDoc.content = document.getElementById('doc-editor-textarea')?.value || changingDoc.content || '';
      }
      _syncLangIcon();
      _syncLangPicker();
      const val = document.getElementById('doc-language-select').value;
      // For form-backed docs, the select toggles between PDF view and the
      // markdown source instead of changing the underlying language.
      const live = document.getElementById('doc-editor-textarea')?.value
        || docs.get(activeDocId)?.content || '';
      if (_isFormBackedDoc(live) && (val === 'pdf' || val === 'markdown')) {
        _setPdfViewActive(val === 'pdf');
        return;
      }
      // Mark user explicitly chose a language — stop auto-detection
      if (activeDocId && docs.has(activeDocId)) {
        docs.get(activeDocId).userSetLanguage = (val !== '');
      }
      updateLanguage();
      syncHighlighting();
      // Show/hide markdown toolbar
      const lang = document.getElementById('doc-language-select').value;
      const mdToolbar = document.getElementById('doc-md-toolbar');
      if (mdToolbar) {
        // Toolbar stays visible for every type now; only the items inside
        // gate themselves on language.
        mdToolbar.style.display = '';
        if (mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
      }
      // If switching away from markdown, exit preview
      if (lang !== 'markdown') {
        _setMarkdownPreviewActive(false);
      }
      if (_isDocxLang(lang)) {
        _setDocxPreviewActive(true);
      } else {
        _setDocxPreviewActive(false);
      }
      // If switching away from CSV, exit table preview
      if (lang !== 'csv') {
        const csvPreview = document.getElementById('doc-csv-preview');
        const wrap2 = document.getElementById('doc-editor-wrap');
        if (csvPreview) csvPreview.style.display = 'none';
        if (wrap2) wrap2.style.display = '';
      }
      // If switching away from html, exit HTML preview
      if (!_isRenderLang(lang)) exitHtmlPreview();
      // Show/hide email fields
      if (lang === 'email') {
        hideMarkdownCompatBanner();
        document.getElementById('doc-email-richbody')?.classList.remove('markdown-visual-mode');
        const doc = activeDocId && docs.get(activeDocId);
        if (doc) _showEmailFields(doc);
      } else if (_isRichTextLang(lang)) {
        _hideEmailFields();
        hideMarkdownCompatBanner();
        const doc = activeDocId && docs.get(activeDocId);
        if (doc) {
          doc.language = lang;
          _showRichTextEditor(doc);
          _scheduleAutoSave(activeDocId, 300);
        }
      } else {
        _hideEmailFields();
        const doc = activeDocId && docs.get(activeDocId);
        if (doc && isVisualMarkdownDoc(doc)) {
          if (resolveMarkdownEditorMode(doc) === 'visual') {
            _showMarkdownVisualEditor(doc);
          } else {
            _hideMarkdownVisualEditor();
            if (doc._visualCompatReasons?.length) renderMarkdownCompatBanner(doc._visualCompatReasons);
            else hideMarkdownCompatBanner();
          }
        } else {
          _hideMarkdownVisualEditor();
          hideMarkdownCompatBanner();
        }
      }
      _syncRichEmptyImport();
      // Sync header action buttons for new language
      _syncHeaderActions();
    });
}
