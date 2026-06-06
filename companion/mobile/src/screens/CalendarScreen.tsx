import { useEffect, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { CalEvent } from '../lib/api';
import { listEvents } from '../lib/api';
import { ChevronLeftIcon } from '../components/icons';

// Read-only agenda: the owner's events for the next 30 days, grouped by day.
export default function CalendarScreen({ conn, onBack }: { conn: Connection; onBack: () => void }) {
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const now = new Date();
    const end = new Date(now.getTime() + 30 * 24 * 3600 * 1000);
    listEvents(conn, now.toISOString(), end.toISOString())
      .then((r) => setEvents(r.events || []))
      .catch(() => setError('Could not load your calendar.'))
      .finally(() => setLoading(false));
  }, [conn]);

  const groups = groupByDay(events);

  return (
    <div className="screen detail">
      <header className="detail-header">
        <button className="ghost" onClick={onBack} type="button" aria-label="Back">
          <ChevronLeftIcon size={24} />
        </button>
        <span className="status-pill">Calendar</span>
      </header>
      <div className="detail-body">
        {error && <div className="error">{error}</div>}
        {loading && <div className="muted pad">Loading...</div>}
        {!loading && !error && events.length === 0 && (
          <div className="muted pad">Nothing in the next 30 days.</div>
        )}
        {groups.map(([day, evs]) => (
          <div key={day} className="agenda-day">
            <div className="agenda-date">{day}</div>
            {evs.map((e, i) => (
              <div key={i} className="agenda-event">
                <div className="agenda-time">{eventTime(e)}</div>
                <div className="agenda-title">{e.title || e.summary || '(untitled)'}</div>
                {e.location && <div className="agenda-loc">{e.location}</div>}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function dayKey(iso?: string): string {
  if (!iso) return 'Undated';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 'Undated';
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function eventTime(e: CalEvent): string {
  if (e.all_day || !e.dtstart) return 'All day';
  const d = new Date(e.dtstart);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

function groupByDay(events: CalEvent[]): [string, CalEvent[]][] {
  const map = new Map<string, CalEvent[]>();
  for (const e of events) {
    const k = dayKey(e.dtstart);
    if (!map.has(k)) map.set(k, []);
    map.get(k)!.push(e);
  }
  return Array.from(map.entries());
}
