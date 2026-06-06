import { useEffect, useState } from 'react';
import type { Connection } from './lib/connection';
import { loadConnection } from './lib/connection';
import PairScreen from './screens/PairScreen';
import SessionsScreen from './screens/SessionsScreen';
import SessionScreen from './screens/SessionScreen';
import NewSessionScreen from './screens/NewSessionScreen';
import SettingsScreen from './screens/SettingsScreen';
import SearchScreen from './screens/SearchScreen';
import ToolsScreen, { type ToolName } from './screens/ToolsScreen';
import EmailScreen from './screens/EmailScreen';
import CalendarScreen from './screens/CalendarScreen';
import NotesScreen from './screens/NotesScreen';
import TasksScreen from './screens/TasksScreen';
import BottomNav, { type Tab } from './components/BottomNav';

// Top-level state machine. Intentionally tiny -- no router dependency for a
// two-tab remote. Until a server is paired we show the pairing screen
// full-bleed; after that it's Sessions / Settings with a session detail
// overlay.
export default function App() {
  const [conn, setConn] = useState<Connection | null>(null);
  const [booted, setBooted] = useState(false);
  const [tab, setTab] = useState<Tab>('sessions');
  const [openSessionId, setOpenSessionId] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [searching, setSearching] = useState(false);
  const [openTool, setOpenTool] = useState<ToolName | null>(null);

  useEffect(() => {
    loadConnection().then((c) => {
      setConn(c);
      setBooted(true);
    });
  }, []);

  if (!booted) {
    return <div className="screen center muted">Loading...</div>;
  }

  if (!conn) {
    return <PairScreen onPaired={setConn} />;
  }

  if (composing) {
    return (
      <NewSessionScreen
        conn={conn}
        onBack={() => setComposing(false)}
        onCreated={(sid) => {
          setComposing(false);
          setOpenSessionId(sid);
        }}
      />
    );
  }

  if (searching) {
    return (
      <SearchScreen
        conn={conn}
        onBack={() => setSearching(false)}
        onOpen={(sid) => {
          setSearching(false);
          setOpenSessionId(sid);
        }}
      />
    );
  }

  if (openSessionId) {
    return (
      <SessionScreen
        conn={conn}
        sessionId={openSessionId}
        onBack={() => setOpenSessionId(null)}
      />
    );
  }

  if (openTool) {
    const back = () => setOpenTool(null);
    if (openTool === 'email') return <EmailScreen conn={conn} onBack={back} />;
    if (openTool === 'calendar') return <CalendarScreen conn={conn} onBack={back} />;
    if (openTool === 'notes') return <NotesScreen conn={conn} onBack={back} />;
    if (openTool === 'tasks') return <TasksScreen conn={conn} onBack={back} />;
  }

  return (
    <div className="app">
      <main className="app-body">
        {tab === 'sessions' && (
          <SessionsScreen
            conn={conn}
            onOpen={setOpenSessionId}
            onNew={() => setComposing(true)}
            onSearch={() => setSearching(true)}
          />
        )}
        {tab === 'tools' && <ToolsScreen onOpen={setOpenTool} />}
        {tab === 'settings' && (
          <SettingsScreen
            conn={conn}
            onDisconnect={() => {
              setConn(null);
              setTab('sessions');
            }}
          />
        )}
      </main>
      <BottomNav active={tab} onChange={setTab} />
    </div>
  );
}
