// static/js/codeServer.js
// Code Server Modal — VS Code in browser via iframe.
import { makeWindowDraggable } from './windowDrag.js';

const _t = (k, v) => (window.__t || (kk => kk))(k, v);

let _open = false;
let _modal = null;

function _createModal() {
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'codeserver-modal';
  modal.innerHTML = `
    <div class="modal-content cs-modal-content">
      <div class="modal-header">
        <h4>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
          ${_t('nav.codeEditor', 'Code Editor')}
        </h4>
        <span style="flex:1"></span>
        <button class="close-btn" id="cs-close-btn" title="${_t('common.close', 'Close')}">&times;</button>
      </div>
      <div class="modal-body cs-modal-body">
        <div id="cs-loading" style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim,#888);font-size:14px;">
          ${_t('codeEditor.loading', 'Loading code-server...')}
        </div>
        <iframe id="codeserver-iframe" src="/api/code-server/"
          style="width:100%;height:100%;border:none;display:none;"
          allow="clipboard-read; clipboard-write"></iframe>
      </div>
    </div>`;

  document.body.appendChild(modal);

  const content = modal.querySelector('.modal-content');
  const header = modal.querySelector('.modal-header');
  if (content && header) {
    makeWindowDraggable(modal, { content, header });
  }

  modal.addEventListener('click', (e) => {
    if (e.target === modal) closePanel();
  });

  const iframe = modal.querySelector('#codeserver-iframe');
  const loading = modal.querySelector('#cs-loading');
  if (iframe) {
    iframe.addEventListener('load', () => {
      if (loading) loading.style.display = 'none';
      iframe.style.display = 'block';
    });
  }

  return modal;
}

export function openPanel() {
  if (_open) return;
  _open = true;
  _modal = _createModal();
  document.getElementById('cs-close-btn')?.addEventListener('click', closePanel);
  document.addEventListener('keydown', _escHandler);
}

function _escHandler(e) {
  if (e.key === 'Escape') closePanel();
}

export function closePanel() {
  if (!_open) return;
  _open = false;
  document.removeEventListener('keydown', _escHandler);
  if (_modal) {
    const content = _modal.querySelector('.modal-content');
    if (content) {
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => _modal?.remove(), { once: true });
      setTimeout(() => { if (_modal?.parentElement) _modal.remove(); }, 250);
    } else {
      _modal.remove();
    }
  }
  _modal = null;
}

export function togglePanel() {
  if (_open) closePanel();
  else openPanel();
}

export function isPanelOpen() {
  return _open;
}

export default { openPanel, closePanel, togglePanel, isPanelOpen };
