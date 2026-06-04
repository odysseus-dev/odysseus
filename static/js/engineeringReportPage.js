import markdownModule from './markdown.js';

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function tokenFromPath() {
  const match = window.location.pathname.match(/^\/engineering\/reports\/([^/]+)\/?$/);
  return match ? decodeURIComponent(match[1]) : '';
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text || '';
}

function renderTimeline(mission) {
  const host = document.getElementById('public-report-timeline');
  if (!host) return;
  const steps = mission.audit_log || [];
  host.innerHTML = steps.map((step) => `
    <div class="engineering-step ${esc(step.status || 'queued')}">
      <span class="engineering-step-dot"></span>
      <span class="engineering-step-copy">
        <span class="engineering-step-title">${esc(step.title || step.stage || 'Step')}</span>
        ${step.detail ? `<span class="engineering-step-detail">${esc(step.detail)}</span>` : ''}
      </span>
    </div>
  `).join('');
}

function configureDownloads(token) {
  const md = document.getElementById('public-export-md');
  const json = document.getElementById('public-export-json');
  if (md) {
    md.href = `/api/engineering-missions/public/${encodeURIComponent(token)}/export/markdown`;
    md.setAttribute('aria-disabled', 'false');
  }
  if (json) {
    json.href = `/api/engineering-missions/public/${encodeURIComponent(token)}/export/json`;
    json.setAttribute('aria-disabled', 'false');
  }
}

async function copyPublicLink() {
  try {
    await navigator.clipboard.writeText(window.location.href);
    setText('public-report-summary', 'Public report link copied.');
  } catch (_) {
    setText('public-report-summary', window.location.href);
  }
}

async function loadReport() {
  const token = tokenFromPath();
  const body = document.getElementById('public-report-body');
  if (!token) {
    if (body) body.innerHTML = '<div class="doclib-empty engineering-error">Missing report token.</div>';
    return;
  }
  try {
    const response = await fetch(`/api/engineering-missions/public/${encodeURIComponent(token)}`, { credentials: 'omit' });
    const mission = await response.json();
    if (!response.ok) throw new Error(mission?.detail || `Report failed to load (${response.status})`);

    document.title = `${mission.title || 'Engineering Mission Report'} — Odysseus`;
    setText('public-report-title', mission.title || 'Engineering Mission Report');
    setText('public-report-summary', mission.summary || '');
    setText('public-report-status', mission.status || 'published');
    const target = document.getElementById('public-report-target');
    if (target) {
      target.href = mission.target_url || '#';
      target.textContent = mission.target_url || '';
    }
    renderTimeline(mission);
    configureDownloads(token);
    if (body) {
      body.innerHTML = markdownModule.mdToHtml(mission.report_markdown || 'No report body is available.');
      try { markdownModule.renderMermaid(body); } catch {}
    }
  } catch (error) {
    setText('public-report-title', 'Report unavailable');
    if (body) body.innerHTML = `<div class="doclib-empty engineering-error">${esc(error.message)}</div>`;
  }
}

document.getElementById('public-copy-link')?.addEventListener('click', copyPublicLink);
loadReport();
