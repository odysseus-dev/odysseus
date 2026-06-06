import { useCallback, useEffect, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { TaskRow } from '../lib/api';
import { listTasks, taskAction } from '../lib/api';
import { ChevronLeftIcon, RefreshIcon } from '../components/icons';

// Scheduled / automation tasks: status at a glance, with pause / resume / run.
export default function TasksScreen({ conn, onBack }: { conn: Connection; onBack: () => void }) {
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    listTasks(conn)
      .then(setTasks)
      .catch(() => setError('Could not load your tasks.'))
      .finally(() => setLoading(false));
  }, [conn]);

  useEffect(refresh, [refresh]);

  async function act(id: string, action: 'pause' | 'resume' | 'run') {
    setBusy(id + action);
    try {
      await taskAction(conn, id, action);
      refresh();
    } catch {
      setError(`Could not ${action} that task.`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="screen detail">
      <header className="detail-header">
        <button className="ghost" onClick={onBack} type="button" aria-label="Back">
          <ChevronLeftIcon size={24} />
        </button>
        <span className="status-pill">Tasks</span>
        <button className="ghost" onClick={refresh} type="button" aria-label="Refresh">
          <RefreshIcon size={20} />
        </button>
      </header>
      <div className="detail-body">
        {error && <div className="error">{error}</div>}
        {loading && <div className="muted pad">Loading...</div>}
        {!loading && !error && tasks.length === 0 && <div className="muted pad">No tasks.</div>}
        {tasks.map((t) => {
          const paused = (t.status || '').toLowerCase() === 'paused';
          return (
            <div key={t.id} className="task-card">
              <div className="task-head">
                <span className={'dot' + (paused ? '' : ' live')} aria-hidden />
                <span className="task-name">{t.name || t.action || '(task)'}</span>
                <span className="task-status">{t.status || ''}</span>
              </div>
              <div className="task-meta">
                {whenLabel(t)}
              </div>
              <div className="task-actions">
                <button
                  className="chip"
                  type="button"
                  disabled={busy !== null}
                  onClick={() => act(t.id, paused ? 'resume' : 'pause')}
                >
                  {paused ? 'Resume' : 'Pause'}
                </button>
                <button
                  className="chip"
                  type="button"
                  disabled={busy !== null}
                  onClick={() => act(t.id, 'run')}
                >
                  {busy === t.id + 'run' ? 'Running...' : 'Run now'}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function whenLabel(t: TaskRow): string {
  if (t.trigger_type && t.trigger_type !== 'schedule') return `Trigger: ${t.trigger_type}`;
  if (t.next_run) {
    const d = new Date(t.next_run);
    if (!isNaN(d.getTime())) return `Next: ${d.toLocaleString()}`;
  }
  if (t.schedule) return `Schedule: ${t.schedule}`;
  return t.task_type || '';
}
