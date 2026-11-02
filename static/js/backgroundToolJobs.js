/** Reconcile server-delivered background replies without resetting chat DOM. */
import { createWhirlpool } from './spinner.js';

const researchSpinners = new WeakMap();
function stopResearchSpinner(node) {
  researchSpinners.get(node)?.destroy();
  researchSpinners.delete(node);
  node.querySelector('[data-research-spinner]')?.replaceChildren();
}

export function deliveryMessages(jobs, existingIds) {
  const seen = new Set(existingIds);
  return jobs.flatMap(job => {
    const message = job.status === 'delivered' && job.message;
    const id = message?.metadata?._db_id;
    if (!id || seen.has(id)) return [];
    seen.add(id);
    return [message];
  });
}

export function researchCardState(job) {
  if (job.outcome === 'error') return { tone: 'error', label: 'Research failed', detail: 'Open research for details.' };
  if (job.outcome === 'no_sources') return { tone: 'error', label: 'No sources found', detail: 'This run did not return source-backed findings.' };
  if (job.status === 'delivered') return { tone: 'done', label: 'Research finished', detail: `${job.source_count ?? '—'} sources · See the update in chat.` };
  if (job.status === 'ready') return { tone: 'running', label: 'Preparing chat update', detail: 'The report is ready. You can keep chatting.' };
  const p = job.progress || {};
  const phase = { probing: 'Checking model', planning: 'Planning', searching: 'Searching', reading: 'Reading sources', analyzing: 'Analyzing findings', writing: 'Writing report' }[p.phase] || 'Starting research';
  const round = p.round ? `Round ${p.round}${job.rounds ? `/${job.rounds}` : ''} · ` : '';
  return { tone: 'running', label: phase, detail: `${round}${p.total_sources ?? 0} sources · You can keep chatting.` };
}

export function renderResearchCards(box, jobs) {
  const visible = jobs.filter(j => j.tool === 'research' && /^[A-Za-z0-9_-]+$/.test(j.id) && j.status !== 'discarded');
  let region = box.querySelector('[data-background-tools-status]');
  if (!visible.length) {
    if (region) for (const card of region.children) stopResearchSpinner(card);
    region?.remove(); return;
  }
  if (!region) {
    region = document.createElement('section');
    region.dataset.backgroundToolsStatus = '';
    region.className = 'background-tools-status agent-thread has-top';
    region.setAttribute('aria-label', 'Chat research');
    box.append(region);
  }
  region.classList.toggle('streaming', visible.some(job => researchCardState(job).tone === 'running'));

  // Keep the live rail immediately after the AI's synthesized response that
  // contains the matching research link. Appending it to the chat history
  // root can place it above the user's next prompt as the conversation grows.
  const origin = [...visible].reverse().map(job => {
    const href = `#research-${job.id}`;
    return [...box.querySelectorAll('.msg-ai')]
      .find(message => [...message.querySelectorAll('a[href]')].some(candidate => candidate.getAttribute('href') === href));
  }).find(Boolean);
  if (origin) origin.insertAdjacentElement('afterend', region);

  const keep = new Set(visible.map(job => job.id));
  for (const card of Array.from(region.children)) if (!keep.has(card.dataset.jobId)) {
    stopResearchSpinner(card);
    card.remove();
  }
  for (const job of visible) {
    let card = Array.from(region.children).find(node => node.dataset.jobId === job.id);
    if (!card) {
      card = document.createElement('article');
      card.dataset.jobId = job.id;
      // Only constant markup; model-authored topics are assigned as text below.
      card.innerHTML = '<div class="agent-thread-dot" aria-hidden="true"></div><button type="button" class="agent-thread-header" aria-expanded="false"><span class="agent-thread-icon" aria-hidden="true"></span><span class="agent-thread-tool">Research</span><span class="agent-thread-status chat-research-background">BG task <span data-research-spinner aria-hidden="true"></span></span><span class="agent-thread-status" data-stage role="status"></span><span class="agent-thread-chevron" aria-hidden="true"></span></button><div class="agent-thread-content"><div class="research-job-query"></div><div class="chat-research-detail"></div><a class="chat-research-open">Open research</a></div>';
      const header = card.querySelector('.agent-thread-header');
      const content = card.querySelector('.agent-thread-content');
      content.id = `chat-research-details-${job.id}`;
      header.setAttribute('aria-controls', content.id);
      header.addEventListener('click', event => {
        // Own this disclosure; do not also trigger the chat's delegated toggle.
        event.stopPropagation();
        header.setAttribute('aria-expanded', String(card.classList.toggle('open')));
      });
      region.append(card);
    }
    const state = researchCardState(job);
    card.className = `agent-thread-node chat-research-card ${state.tone}${card.classList.contains('open') ? ' open' : ''}`;
    const setText = (selector, text) => {
      const node = card.querySelector(selector);
      if (node.textContent !== text) node.textContent = text;
    };
    setText('.research-job-query', job.query || 'Research');
    setText('[data-stage]', state.label);
    setText('.agent-thread-icon', state.tone === 'error' ? '✗' : state.tone === 'done' ? '✓' : '·');
    setText('.chat-research-detail', state.detail);
    if (state.tone === 'running' && !researchSpinners.has(card)) {
      const spinner = createWhirlpool(16);
      card.querySelector('[data-research-spinner]').append(spinner.element);
      researchSpinners.set(card, spinner);
    } else if (state.tone !== 'running') stopResearchSpinner(card);
    card.querySelector('a').href = `#research-${job.id}`;
  }
}

export function startBackgroundToolJobs({ getSessionId, addMessage, base = '' }) {
  let inFlight = false;
  const busy = () => Boolean(document.querySelector('#chat-history .msg-ai.streaming'));
  const tick = async () => {
    const sid = getSessionId();
    if (inFlight || !sid || document.hidden || window.__odysseusSessionReadyId !== sid) return;
    inFlight = true;
    try {
      const response = await fetch(`${base}/api/research/chat-jobs/${encodeURIComponent(sid)}`, { credentials: 'same-origin' });
      if (!response.ok) return;
      const { jobs = [] } = await response.json();
      if (getSessionId() !== sid || window.__odysseusSessionReadyId !== sid) return;
      const box = document.querySelector('#chat-history');
      if (!box) return;
      renderResearchCards(box, jobs);
      if (busy()) return;
      const ids = Array.from(box.querySelectorAll('[data-db-id]'), node => node.dataset.dbId);
      for (const message of deliveryMessages(jobs, ids)) {
        addMessage(message.role, message.content, message.metadata?.model, message.metadata);
      }
    } catch { /* A transient poll error must not disrupt foreground chat. */ }
    finally { inFlight = false; }
  };
  const timer = setInterval(tick, 3000);
  const onVisible = () => { if (!document.hidden) void tick(); };
  document.addEventListener('visibilitychange', onVisible);
  void tick();
  return () => { clearInterval(timer); document.removeEventListener('visibilitychange', onVisible); };
}
