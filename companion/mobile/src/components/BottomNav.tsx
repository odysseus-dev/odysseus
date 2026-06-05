import type { ComponentType } from 'react';
import { SessionsIcon, SettingsIcon } from './icons';

export type Tab = 'sessions' | 'settings';

const TABS: { id: Tab; label: string; Icon: ComponentType<{ size?: number }> }[] = [
  { id: 'sessions', label: 'Sessions', Icon: SessionsIcon },
  { id: 'settings', label: 'Settings', Icon: SettingsIcon },
];

export default function BottomNav({
  active,
  onChange,
}: {
  active: Tab;
  onChange: (t: Tab) => void;
}) {
  return (
    <nav className="bottom-nav">
      {TABS.map(({ id, label, Icon }) => (
        <button
          key={id}
          className={'nav-item' + (active === id ? ' active' : '')}
          onClick={() => onChange(id)}
          type="button"
        >
          <span className="nav-icon">
            <Icon size={22} />
          </span>
          <span className="nav-label">{label}</span>
        </button>
      ))}
    </nav>
  );
}
