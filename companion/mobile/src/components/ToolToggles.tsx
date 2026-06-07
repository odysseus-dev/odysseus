import type { ReactNode } from 'react';
import type { ChatOptions } from '../lib/api';
import { BotIcon, GlobeIcon, ResearchIcon, TerminalIcon } from './icons';

// The per-turn capability chips that mirror the desktop's chat toggles. Agent
// mode is the master switch for tools; terminal (bash) is an agent-only tool,
// so turning it on implies agent mode and turning agent off clears it. Deep
// research is a self-contained mode, mutually exclusive with the agent tools.
export default function ToolToggles({
  value,
  onChange,
  disabled,
}: {
  value: ChatOptions;
  onChange: (next: ChatOptions) => void;
  disabled?: boolean;
}) {
  function set(patch: Partial<ChatOptions>) {
    const next = { ...value, ...patch };
    if (patch.research !== undefined) {
      // Turning research on clears the agent tools (research does its own work).
      if (next.research) {
        next.agent = false;
        next.terminal = false;
        next.web = false;
      }
    } else if (next.agent || next.web || next.terminal) {
      // Touching an agent tool drops research.
      next.research = false;
    }
    if (next.terminal) next.agent = true; // bash only exists as an agent tool
    if (!next.agent) next.terminal = false; // no tools without agent mode
    onChange(next);
  }

  return (
    <div className="toggles">
      <Chip
        active={value.agent}
        disabled={disabled}
        onClick={() => set({ agent: !value.agent })}
        icon={<BotIcon size={16} />}
        label="Agent"
      />
      <Chip
        active={value.web}
        disabled={disabled}
        onClick={() => set({ web: !value.web })}
        icon={<GlobeIcon size={16} />}
        label="Web"
      />
      <Chip
        active={value.terminal}
        disabled={disabled}
        onClick={() => set({ terminal: !value.terminal })}
        icon={<TerminalIcon size={16} />}
        label="Terminal"
      />
      <Chip
        active={value.research}
        disabled={disabled}
        onClick={() => set({ research: !value.research })}
        icon={<ResearchIcon size={16} />}
        label="Research"
      />
    </div>
  );
}

function Chip({
  active,
  disabled,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      className={'chip' + (active ? ' active' : '')}
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
