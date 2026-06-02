// Scheduler Calendar — a Kuroryuu-style visual layer over Odysseus's existing
// /api/tasks scheduler backend. Adds what the list-only tasks.js lacks:
//   * month CALENDAR grid of scheduled jobs (by next_run)
//   * DAY MODAL with a 24-hour timeline + inline create/edit
//   * unified run-HISTORY / activity panel
//
// Reads/writes the EXISTING task API (no backend changes): GET /api/tasks,
// POST /api/tasks, DELETE /api/tasks/{id}, POST /api/tasks/{id}/run|pause|resume,
// GET /api/tasks/{id}/runs. Self-registering panel (terminal.js pattern).

let _injected = false;
let _tasks = [];
let _viewYear, _viewMonth;          // currently displayed month
let _historyTaskId = null;

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                'August', 'September', 'October', 'November', 'December'];

async function api(path, opts = {}) {
  const res = await fetch('/api/tasks' + path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  return res;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// ---- data ------------------------------------------------------------------

async function loadTasks() {
  try {
    const r = await api('');
    const data = await r.json();
    _tasks = Array.isArray(data) ? data : (data.tasks || []);
  } catch (e) {
    _tasks = [];
  }
}

function tasksOnDate(year, month, day) {
  // A task lands on a calendar day if its next_run is that day.
  return _tasks.filter(t => {
    if (!t.next_run) return false;
    const d = new Date(t.next_run);
    return d.getFullYear() === year && d.getMonth() === month && d.getDate() === day;
  });
}

function scheduleLabel(t) {
  const time = t.scheduled_time || '';
  switch (t.schedule) {
    case 'once': return t.scheduled_date ? `Once · ${new Date(t.scheduled_date).toLocaleString()}` : 'Once';
    case 'daily': return `Daily · ${time}`;
    case 'weekly': return `Weekly · ${WEEKDAYS[((t.scheduled_day ?? 0) + 1) % 7] || ''} ${time}`;
    case 'monthly': return `Monthly · day ${t.scheduled_day ?? 1} ${time}`;
    case 'cron': return `Cron · ${t.cron_expression || ''}`;
    default: return t.trigger_type === 'event' ? `On ${t.trigger_event}` :
                    t.trigger_type === 'webhook' ? 'Webhook' : (t.schedule || '—');
  }
}

// ---- markup ----------------------------------------------------------------

function injectMarkup() {
  if (_injected) return;
  _injected = true;

  const btn = document.createElement('button');
  btn.id = 'scheduler-cal-btn';
  btn.title = 'Scheduler calendar';
  btn.textContent = 'Schedule';
  btn.style.cssText =
    'position:fixed;bottom:14px;right:280px;z-index:240;padding:6px 12px;' +
    'font-size:0.8rem;border-radius:6px;cursor:pointer;' +
    'background:var(--panel,#1e1e1e);color:var(--fg,#eee);' +
    'border:1px solid var(--border,#444);opacity:0.82;';
  btn.addEventListener('mouseenter', () => (btn.style.opacity = '1'));
  btn.addEventListener('mouseleave', () => (btn.style.opacity = '0.82'));
  btn.addEventListener('click', () => toggle());
  document.body.appendChild(btn);

  const modal = document.createElement('div');
  modal.id = 'scheduler-cal-modal';
  modal.className = 'modal hidden';
  modal.innerHTML =
    '<div class="modal-content" style="width:min(960px,96vw);max-height:90vh;' +
    'display:flex;flex-direction:column;background:var(--bg,#111);overflow:hidden;">' +
      '<div class="modal-header" style="cursor:move;">' +
        '<h4 style="margin:0;">Scheduler</h4>' +
        '<button class="close-btn" id="close-scheduler-cal" aria-label="Close">✖</button>' +
      '</div>' +
      '<div class="modal-body" style="flex:1;overflow:auto;padding:10px 14px;">' +
        '<div id="sched-cal-nav" style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">' +
          '<button id="sched-prev" class="btn" style="padding:3px 10px;cursor:pointer;">‹</button>' +
          '<span id="sched-month-label" style="font-weight:600;min-width:160px;text-align:center;"></span>' +
          '<button id="sched-next" class="btn" style="padding:3px 10px;cursor:pointer;">›</button>' +
          '<button id="sched-today" class="btn" style="padding:3px 10px;cursor:pointer;">Today</button>' +
          '<span style="flex:1;"></span>' +
          '<button id="sched-activity" class="btn" style="padding:3px 10px;cursor:pointer;">Activity</button>' +
          '<button id="sched-newjob" class="btn" style="padding:3px 10px;cursor:pointer;">+ New job</button>' +
        '</div>' +
        '<div id="sched-calendar"></div>' +
        '<div id="sched-daymodal"></div>' +
        '<div id="sched-history"></div>' +
      '</div>' +
    '</div>';
  document.body.appendChild(modal);

  modal.querySelector('#close-scheduler-cal').addEventListener('click', () => close());
  modal.querySelector('#sched-prev').addEventListener('click', () => { shiftMonth(-1); });
  modal.querySelector('#sched-next').addEventListener('click', () => { shiftMonth(1); });
  modal.querySelector('#sched-today').addEventListener('click', () => { const n = new Date(); _viewYear = n.getFullYear(); _viewMonth = n.getMonth(); renderCalendar(); });
  modal.querySelector('#sched-activity').addEventListener('click', () => showActivity());
  modal.querySelector('#sched-newjob').addEventListener('click', () => showJobEditor(null, null));
}

function shiftMonth(delta) {
  _viewMonth += delta;
  if (_viewMonth < 0) { _viewMonth = 11; _viewYear--; }
  if (_viewMonth > 11) { _viewMonth = 0; _viewYear++; }
  renderCalendar();
}

// ---- calendar grid ---------------------------------------------------------

function renderCalendar() {
  const label = document.getElementById('sched-month-label');
  if (label) label.textContent = `${MONTHS[_viewMonth]} ${_viewYear}`;
  document.getElementById('sched-daymodal').innerHTML = '';
  document.getElementById('sched-history').innerHTML = '';

  const first = new Date(_viewYear, _viewMonth, 1);
  const startPad = first.getDay();                       // 0=Sun
  const daysInMonth = new Date(_viewYear, _viewMonth + 1, 0).getDate();
  const today = new Date();

  let cells = '';
  // weekday header
  cells += `<div class="sched-row sched-head">` +
    WEEKDAYS.map(w => `<div class="sched-cell sched-wd">${w}</div>`).join('') + `</div>`;

  let day = 1 - startPad;
  for (let week = 0; week < 6 && day <= daysInMonth; week++) {
    cells += '<div class="sched-row">';
    for (let dow = 0; dow < 7; dow++, day++) {
      if (day < 1 || day > daysInMonth) {
        cells += `<div class="sched-cell sched-empty"></div>`;
        continue;
      }
      const jobs = tasksOnDate(_viewYear, _viewMonth, day);
      const isToday = today.getFullYear() === _viewYear && today.getMonth() === _viewMonth && today.getDate() === day;
      const badges = jobs.slice(0, 3).map(j => {
        const t = j.next_run ? new Date(j.next_run).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
        const color = j.status === 'paused' ? 'var(--fg,#888)' : (j.status === 'completed' ? 'var(--fg,#5a7)' : 'var(--fg,#5b9bd5)');
        return `<div class="sched-badge" style="border-left:3px solid ${color};" title="${esc(j.name)}">${t} ${esc((j.name || '').slice(0, 16))}</div>`;
      }).join('');
      const more = jobs.length > 3 ? `<div class="sched-more">+${jobs.length - 3} more</div>` : '';
      cells += `<div class="sched-cell sched-day${isToday ? ' sched-today' : ''}" data-day="${day}">` +
        `<div class="sched-daynum">${day}</div>${badges}${more}</div>`;
    }
    cells += '</div>';
  }

  document.getElementById('sched-calendar').innerHTML =
    `<style>
      .sched-row{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;margin-bottom:3px;}
      .sched-cell{min-height:84px;border:1px solid var(--border,#333);border-radius:5px;padding:3px;font-size:0.72rem;overflow:hidden;}
      .sched-head .sched-cell{min-height:0;}
      .sched-wd{text-align:center;font-weight:600;opacity:0.7;border:none;}
      .sched-empty{border-color:transparent;background:transparent;}
      .sched-day{cursor:pointer;background:var(--panel,#1a1a1a);}
      .sched-day:hover{outline:1px solid var(--accent,#5b9bd5);}
      .sched-today{outline:2px solid var(--accent,#5b9bd5);}
      .sched-daynum{font-weight:600;opacity:0.8;margin-bottom:2px;}
      .sched-badge{background:var(--bg,#0c0c0c);border-radius:3px;padding:1px 4px;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
      .sched-more{opacity:0.6;font-size:0.68rem;}
    </style>` + cells;

  document.querySelectorAll('#sched-calendar .sched-day').forEach(c =>
    c.addEventListener('click', () => showDay(parseInt(c.dataset.day, 10))));
}

// ---- day modal (24h timeline) ----------------------------------------------

function showDay(day) {
  const jobs = tasksOnDate(_viewYear, _viewMonth, day)
    .sort((a, b) => new Date(a.next_run) - new Date(b.next_run));
  const dateStr = `${MONTHS[_viewMonth]} ${day}, ${_viewYear}`;

  let hours = '';
  for (let h = 0; h < 24; h++) {
    const hj = jobs.filter(j => new Date(j.next_run).getHours() === h);
    const label = `${(h % 12) || 12} ${h < 12 ? 'AM' : 'PM'}`;
    const blocks = hj.map(j =>
      `<div class="sched-jobblock" data-id="${esc(j.id)}" style="cursor:pointer;">` +
      `${esc(j.name)} <span style="opacity:0.6;">${esc(scheduleLabel(j))}</span></div>`).join('');
    hours += `<div class="sched-hour" data-hour="${h}">` +
      `<span class="sched-hlabel">${label}</span><div class="sched-hbody">${blocks}` +
      `<button class="sched-addhere" data-hour="${h}" title="Add job at ${label}">+</button></div></div>`;
  }

  document.getElementById('sched-history').innerHTML = '';
  document.getElementById('sched-daymodal').innerHTML =
    `<style>
      .sched-hour{display:flex;border-bottom:1px solid var(--border,#222);min-height:26px;}
      .sched-hlabel{width:64px;flex-shrink:0;opacity:0.6;font-size:0.72rem;padding:3px 6px;}
      .sched-hbody{flex:1;padding:2px;display:flex;flex-wrap:wrap;gap:4px;align-items:center;}
      .sched-jobblock{background:var(--panel,#1e1e1e);border-left:3px solid var(--fg,#5b9bd5);border-radius:3px;padding:2px 6px;font-size:0.74rem;}
      .sched-addhere{margin-left:auto;background:transparent;border:1px dashed var(--border,#444);color:var(--fg,#888);border-radius:3px;cursor:pointer;width:20px;height:20px;line-height:1;}
      .sched-addhere:hover{border-style:solid;color:var(--accent,#5b9bd5);}
     </style>` +
    `<div style="margin-top:10px;border:1px solid var(--border,#333);border-radius:6px;overflow:hidden;">` +
      `<div style="display:flex;align-items:center;padding:6px 10px;background:var(--panel,#1a1a1a);">` +
        `<b>${dateStr}</b><span style="opacity:0.6;margin-left:8px;">${jobs.length} scheduled</span>` +
        `<span style="flex:1;"></span>` +
        `<button id="sched-day-close" class="btn" style="padding:2px 10px;cursor:pointer;">Back to month</button>` +
      `</div>` +
      `<div style="max-height:380px;overflow:auto;">${hours}</div>` +
    `</div>`;

  document.getElementById('sched-calendar').style.display = 'none';
  document.getElementById('sched-day-close').addEventListener('click', () => {
    document.getElementById('sched-daymodal').innerHTML = '';
    document.getElementById('sched-calendar').style.display = '';
  });
  document.querySelectorAll('#sched-daymodal .sched-jobblock').forEach(b =>
    b.addEventListener('click', () => {
      const t = _tasks.find(x => x.id === b.dataset.id);
      if (t) showJobEditor(t, day);
    }));
  document.querySelectorAll('#sched-daymodal .sched-addhere').forEach(b =>
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      showJobEditor(null, day, parseInt(b.dataset.hour, 10));
    }));
}

// ---- job editor ------------------------------------------------------------

function showJobEditor(task, day, hour) {
  const isEdit = !!task;
  const t = task || {};
  const defTime = (hour != null) ? `${String(hour).padStart(2, '0')}:00` : (t.scheduled_time || '09:00');
  const sched = t.schedule || 'daily';

  const host = document.getElementById('sched-daymodal');
  document.getElementById('sched-history').innerHTML = '';
  document.getElementById('sched-calendar').style.display = 'none';
  host.innerHTML =
    `<div style="margin-top:10px;border:1px solid var(--border,#333);border-radius:6px;padding:14px;background:var(--panel,#161616);">` +
      `<h4 style="margin:0 0 10px;">${isEdit ? 'Edit job' : 'New job'}</h4>` +
      `<label style="display:block;margin-bottom:8px;">Name<br>` +
        `<input id="je-name" value="${esc(t.name || '')}" style="width:100%;padding:5px 8px;background:var(--bg,#0c0c0c);color:var(--fg,#eee);border:1px solid var(--border,#444);border-radius:4px;"></label>` +
      `<label style="display:block;margin-bottom:8px;">Prompt (LLM task)<br>` +
        `<textarea id="je-prompt" rows="3" style="width:100%;padding:5px 8px;background:var(--bg,#0c0c0c);color:var(--fg,#eee);border:1px solid var(--border,#444);border-radius:4px;">${esc(t.prompt || '')}</textarea></label>` +
      `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px;">` +
        `<label>Schedule<br><select id="je-schedule" style="padding:5px 8px;background:var(--bg,#0c0c0c);color:var(--fg,#eee);border:1px solid var(--border,#444);border-radius:4px;">` +
          ['once', 'daily', 'weekly', 'monthly', 'cron'].map(s => `<option value="${s}"${s === sched ? ' selected' : ''}>${s}</option>`).join('') +
        `</select></label>` +
        `<label id="je-time-wrap">Time (HH:MM UTC)<br><input id="je-time" value="${esc(defTime)}" style="padding:5px 8px;width:90px;background:var(--bg,#0c0c0c);color:var(--fg,#eee);border:1px solid var(--border,#444);border-radius:4px;"></label>` +
        `<label id="je-day-wrap" style="display:none;">Day<br><input id="je-day" type="number" value="${t.scheduled_day ?? 1}" style="padding:5px 8px;width:70px;background:var(--bg,#0c0c0c);color:var(--fg,#eee);border:1px solid var(--border,#444);border-radius:4px;"></label>` +
        `<label id="je-cron-wrap" style="display:none;">Cron<br><input id="je-cron" value="${esc(t.cron_expression || '*/30 * * * *')}" style="padding:5px 8px;width:140px;background:var(--bg,#0c0c0c);color:var(--fg,#eee);border:1px solid var(--border,#444);border-radius:4px;"></label>` +
      `</div>` +
      `<div style="display:flex;gap:8px;margin-top:6px;flex-wrap:wrap;">` +
        `<button id="je-save" class="btn" style="padding:5px 14px;cursor:pointer;">${isEdit ? 'Save' : 'Create'}</button>` +
        (isEdit ? `<button id="je-run" class="btn" style="padding:5px 12px;cursor:pointer;">Run now</button>` : '') +
        (isEdit ? `<button id="je-history" class="btn" style="padding:5px 12px;cursor:pointer;">History</button>` : '') +
        (isEdit ? `<button id="je-${(t.status === 'paused') ? 'resume' : 'pause'}" class="btn" style="padding:5px 12px;cursor:pointer;">${t.status === 'paused' ? 'Resume' : 'Pause'}</button>` : '') +
        (isEdit ? `<button id="je-delete" class="btn" style="padding:5px 12px;cursor:pointer;color:var(--color-error,#d66);">Delete</button>` : '') +
        `<button id="je-cancel" class="btn" style="padding:5px 12px;cursor:pointer;margin-left:auto;">Cancel</button>` +
      `</div>` +
      `<div id="je-msg" style="margin-top:8px;font-size:0.78rem;opacity:0.7;min-height:1em;"></div>` +
    `</div>`;

  const schedSel = document.getElementById('je-schedule');
  function syncFields() {
    const v = schedSel.value;
    document.getElementById('je-time-wrap').style.display = (v === 'cron') ? 'none' : '';
    document.getElementById('je-day-wrap').style.display = (v === 'weekly' || v === 'monthly') ? '' : 'none';
    document.getElementById('je-cron-wrap').style.display = (v === 'cron') ? '' : 'none';
  }
  schedSel.addEventListener('change', syncFields); syncFields();

  document.getElementById('je-cancel').addEventListener('click', () => { renderCalendar(); document.getElementById('sched-calendar').style.display = ''; });
  document.getElementById('je-save').addEventListener('click', () => saveJob(isEdit ? t.id : null));
  if (isEdit) {
    document.getElementById('je-run').addEventListener('click', async () => {
      const m = document.getElementById('je-msg'); m.textContent = 'running…';
      const r = await api(`/${t.id}/run`, { method: 'POST' });
      m.textContent = r.ok ? 'triggered — see History' : ('run failed (' + r.status + ')');
    });
    document.getElementById('je-history').addEventListener('click', () => showHistory(t.id, t.name));
    const pb = document.getElementById('je-pause') || document.getElementById('je-resume');
    pb.addEventListener('click', async () => {
      const act = t.status === 'paused' ? 'resume' : 'pause';
      await api(`/${t.id}/${act}`, { method: 'POST' });
      await loadTasks(); renderCalendar(); document.getElementById('sched-calendar').style.display = '';
    });
    document.getElementById('je-delete').addEventListener('click', async () => {
      await api(`/${t.id}`, { method: 'DELETE' });
      await loadTasks(); renderCalendar(); document.getElementById('sched-calendar').style.display = '';
    });
  }
}

async function saveJob(taskId) {
  const msg = document.getElementById('je-msg');
  const body = {
    name: document.getElementById('je-name').value.trim() || null,
    prompt: document.getElementById('je-prompt').value.trim() || null,
    task_type: 'llm',
    schedule: document.getElementById('je-schedule').value,
    scheduled_time: document.getElementById('je-time').value.trim() || '09:00',
    output_target: 'notification',
  };
  const sv = body.schedule;
  if (sv === 'weekly' || sv === 'monthly') body.scheduled_day = parseInt(document.getElementById('je-day').value, 10);
  if (sv === 'cron') body.cron_expression = document.getElementById('je-cron').value.trim();
  if (!body.prompt) { msg.textContent = 'prompt is required for an LLM task'; return; }
  msg.textContent = 'saving…';
  const r = taskId
    ? await api(`/${taskId}`, { method: 'PUT', body: JSON.stringify(body) })
    : await api('', { method: 'POST', body: JSON.stringify(body) });
  if (r.ok) {
    await loadTasks();
    renderCalendar();
    document.getElementById('sched-calendar').style.display = '';
  } else {
    msg.textContent = 'save failed (' + r.status + '): ' + (await r.text()).slice(0, 200);
  }
}

// ---- history / activity ----------------------------------------------------

async function showHistory(taskId, name) {
  _historyTaskId = taskId;
  const r = await api(`/${taskId}/runs?limit=50`);
  const data = r.ok ? await r.json() : [];
  const runs = Array.isArray(data) ? data : (data.runs || []);
  renderRuns(`Run history — ${esc(name || taskId)}`, runs);
}

async function showActivity() {
  const r = await api('/runs/recent?limit=80');
  const data = r.ok ? await r.json() : [];
  const runs = Array.isArray(data) ? data : (data.runs || []);
  renderRuns('Recent activity (all jobs)', runs);
}

function renderRuns(title, runs) {
  document.getElementById('sched-calendar').style.display = 'none';
  document.getElementById('sched-daymodal').innerHTML = '';
  const statusColor = s => ({ success: 'var(--fg,#5a7)', error: 'var(--color-error,#d55)', running: 'var(--fg,#5b9bd5)', queued: 'var(--fg,#aa7)', skipped: 'var(--fg,#888)', aborted: 'var(--color-error,#a66)' }[s] || 'var(--fg,#888)');
  const rows = runs.length ? runs.map(r => {
    const dur = (r.started_at && r.finished_at)
      ? Math.round((new Date(r.finished_at) - new Date(r.started_at)) / 1000) + 's' : '';
    const when = r.started_at ? new Date(r.started_at).toLocaleString() : '';
    const snippet = esc((r.result || r.error || '').slice(0, 160));
    return `<div style="padding:6px 8px;border-bottom:1px solid var(--border,#222);font-size:0.78rem;">` +
      `<span style="color:${statusColor(r.status)};font-weight:600;">${esc(r.status)}</span> ` +
      `<span style="opacity:0.7;">${when}${dur ? ' · ' + dur : ''}${r.tokens_used ? ' · ' + r.tokens_used + ' tok' : ''}</span>` +
      (snippet ? `<div style="opacity:0.8;margin-top:2px;">${snippet}</div>` : '') +
    `</div>`;
  }).join('') : '<div style="opacity:0.6;padding:10px;">No runs yet.</div>';

  document.getElementById('sched-history').innerHTML =
    `<div style="margin-top:10px;border:1px solid var(--border,#333);border-radius:6px;overflow:hidden;">` +
      `<div style="display:flex;align-items:center;padding:6px 10px;background:var(--panel,#1a1a1a);">` +
        `<b>${title}</b><span style="flex:1;"></span>` +
        `<button id="sched-hist-close" class="btn" style="padding:2px 10px;cursor:pointer;">Back to month</button></div>` +
      `<div style="max-height:420px;overflow:auto;">${rows}</div>` +
    `</div>`;
  document.getElementById('sched-hist-close').addEventListener('click', () => {
    document.getElementById('sched-history').innerHTML = '';
    document.getElementById('sched-calendar').style.display = '';
  });
}

// ---- panel lifecycle -------------------------------------------------------

export async function open() {
  injectMarkup();
  const modal = document.getElementById('scheduler-cal-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  const n = new Date();
  if (_viewYear == null) { _viewYear = n.getFullYear(); _viewMonth = n.getMonth(); }
  document.getElementById('sched-calendar').style.display = '';
  await loadTasks();
  renderCalendar();
}

export function close() {
  const modal = document.getElementById('scheduler-cal-modal');
  if (modal) modal.classList.add('hidden');
}

export function isVisible() {
  const modal = document.getElementById('scheduler-cal-modal');
  return !!modal && !modal.classList.contains('hidden');
}

export function toggle() { if (isVisible()) close(); else open(); }

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', injectMarkup);
} else {
  injectMarkup();
}

const schedulerCalendarModule = { open, close, isVisible, toggle };
export default schedulerCalendarModule;
if (typeof window !== 'undefined') window.schedulerCalendarModule = schedulerCalendarModule;
