import {
  CalendarIcon,
  CheckSquareIcon,
  ChevronRightIcon,
  MailIcon,
  NoteIcon,
} from '../components/icons';

export type ToolName = 'email' | 'calendar' | 'notes' | 'tasks';

// The Tools tab: a menu into each owner-scoped tool. Keeps the bottom nav small
// (Sessions / Tools / Settings) while the tools live one tap in.
export default function ToolsScreen({ onOpen }: { onOpen: (t: ToolName) => void }) {
  const items: { key: ToolName; label: string; sub: string; icon: JSX.Element }[] = [
    { key: 'email', label: 'Email', sub: 'Read and reply to your mail', icon: <MailIcon size={22} /> },
    { key: 'calendar', label: 'Calendar', sub: 'Upcoming events', icon: <CalendarIcon size={22} /> },
    { key: 'notes', label: 'Notes', sub: 'Notes and checklists', icon: <NoteIcon size={22} /> },
    { key: 'tasks', label: 'Tasks', sub: 'Scheduled and automation tasks', icon: <CheckSquareIcon size={22} /> },
  ];
  return (
    <div className="screen list">
      <header className="list-header">
        <h1>Tools</h1>
      </header>
      <ul className="rows">
        {items.map((it) => (
          <li key={it.key}>
            <button className="row" onClick={() => onOpen(it.key)} type="button">
              <span className="fs-icon">{it.icon}</span>
              <span className="row-main">
                <span className="row-title">{it.label}</span>
                <span className="row-sub">
                  <span>{it.sub}</span>
                </span>
              </span>
              <span className="chev">
                <ChevronRightIcon size={20} />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
