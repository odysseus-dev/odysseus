// static/js/planWindow.js
//
// Plan mode: show a proposed plan in a draggable, side-dockable window —
// reusing the same modal + makeWindowDraggable framework the calendar, email,
// and document panels use. Approving from here runs the plan with full tools.

import uiModule from './ui.js';
import markdownModule from './markdown.js';
import { makeWindowDraggable } from './windowDrag.js';

let _modal = null;
let _onApprove = null;

function _getModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'plan-window';
  _modal.className = 'modal';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content plan-window-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg><span id="plan-window-title">Proposed plan</span></h4>
        <button class="close-btn" id="plan-window-close">✖</button>
      </div>
      <div class="modal-body plan-window-body" id="plan-window-body"></div>
      <div class="modal-footer plan-window-footer">
        <button type="button" class="plan-approve-btn" id="plan-window-approve">Approve &amp; Run</button>
      </div>
    </div>`;
  document.body.appendChild(_modal);
  _modal.querySelector('#plan-window-close').addEventListener('click', closePlanWindow);
  _modal.querySelector('#plan-window-approve').addEventListener('click', () => {
    const cb = _onApprove;
    closePlanWindow();
    if (typeof cb === 'function') cb();
  });
  // Draggable + side-dockable, same one-call helper as the other windows.
  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_modal, { content, header });
  return _modal;
}

/**
 * Open the plan window with rendered markdown and an approve callback.
 * @param {string} planMarkdown - the agent's proposed plan (raw markdown)
 * @param {Function} onApprove - called when the user clicks Approve & Run
 */
export function openPlanWindow(planMarkdown, onApprove) {
  const modal = _getModal();
  _onApprove = onApprove || null;
  const body = modal.querySelector('#plan-window-body');
  if (body) {
    body.innerHTML = markdownModule.processWithThinking(
      markdownModule.squashOutsideCode(planMarkdown || '')
    );
    if (window.hljs) body.querySelectorAll('pre code').forEach((b) => window.hljs.highlightElement(b));
  }
  const approveBtn = modal.querySelector('#plan-window-approve');
  if (approveBtn) approveBtn.style.display = onApprove ? '' : 'none';
  // Title reflects state: still awaiting approval (approve callback present) vs
  // already approved and being executed.
  const title = modal.querySelector('#plan-window-title');
  if (title) title.textContent = onApprove ? 'Proposed plan' : 'Approved plan';
  modal.style.display = 'flex';
  if (uiModule && uiModule.scrollHistory) { try { uiModule.scrollHistory(); } catch (_) {} }
}

export function closePlanWindow() {
  if (_modal) _modal.style.display = 'none';
}

/** True when the plan window is currently visible (for live-refresh on progress). */
export function isPlanWindowOpen() {
  return !!(_modal && _modal.style.display !== 'none');
}

export default { openPlanWindow, closePlanWindow, isPlanWindowOpen };
