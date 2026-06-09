import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleAlert,
  Database,
  FileText,
  MessageSquarePlus,
  Power,
  RefreshCw,
  RotateCw,
  Save,
  Search,
  Send,
  Settings as SettingsIcon,
  Trash2,
  Upload
} from "lucide-react";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  AppStatus,
  ChatResult,
  DocumentImportResult,
  DocumentRecord,
  LegacyImportReport,
  Message,
  OCRDependencyName,
  OCRResult,
  OCRStatus,
  OllamaStatus,
  RAGHealth,
  RAGIndexResult,
  RAGSearchResult,
  Session,
  Settings,
  getAppStatus,
  rpc
} from "./tauri";

type LoadState = "idle" | "loading" | "error";
type ActiveView = "chat" | "documents";
const SUPPORTED_DOCUMENT_EXTENSIONS = [".txt", ".md", ".pdf"];

function App() {
  const [appStatus, setAppStatus] = useState<AppStatus | null>(null);
  const [settings, setSettings] = useState<Settings>({});
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [ollama, setOllama] = useState<OllamaStatus | null>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [ragHealth, setRagHealth] = useState<RAGHealth | null>(null);
  const [ocrStatus, setOcrStatus] = useState<OCRStatus | null>(null);
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [draft, setDraft] = useState("");
  const [modelDraft, setModelDraft] = useState("");
  const [importPath, setImportPath] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<RAGSearchResult[]>([]);
  const [retrievedChunks, setRetrievedChunks] = useState<RAGSearchResult[]>([]);
  const [useRag, setUseRag] = useState(false);
  const [lastIndexResult, setLastIndexResult] = useState<RAGIndexResult | null>(null);
  const [lastOcrResult, setLastOcrResult] = useState<OCRResult | null>(null);
  const [legacyPath, setLegacyPath] = useState("");
  const [legacyReport, setLegacyReport] = useState<LegacyImportReport | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedSession = useMemo(
    () => sessions.find((session) => session.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId]
  );

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    let disposed = false;
    let unlisten: (() => void) | undefined;

    void getCurrentWindow()
      .onDragDropEvent((event) => {
        if (event.payload.type !== "drop") return;
        const [path] = event.payload.paths;
        if (!path || disposed) return;
        setActiveView("documents");
        void importDocumentPath(path);
      })
      .then((nextUnlisten) => {
        if (disposed) {
          nextUnlisten();
          return;
        }
        unlisten = nextUnlisten;
      })
      .catch((err) => setError(readError(err)));

    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    if (!selectedSessionId) {
      setMessages([]);
      return;
    }
    void loadMessages(selectedSessionId);
  }, [selectedSessionId]);

  async function bootstrap() {
    setLoadState("loading");
    setError(null);
    try {
      const [status, nextSettings, nextSessions, nextOllama, nextDocuments, nextHealth, nextOcr] =
        await Promise.all([
          getAppStatus(),
          rpc<Settings>("settings.get"),
          rpc<Session[]>("sessions.list"),
          rpc<OllamaStatus>("models.detect_ollama"),
          rpc<DocumentRecord[]>("documents.list"),
          rpc<RAGHealth>("rag.health"),
          rpc<OCRStatus>("ocr.status")
        ]);
      setAppStatus(status);
      setSettings(nextSettings);
      setModelDraft(String(nextSettings.default_model ?? "llama3.2"));
      setSessions(nextSessions);
      setOllama(nextOllama);
      setDocuments(nextDocuments);
      setRagHealth(nextHealth);
      setOcrStatus(nextOcr);
      setSelectedSessionId((current) => current ?? nextSessions[0]?.id ?? null);
      setLoadState("idle");
    } catch (err) {
      setLoadState("error");
      setError(readError(err));
    }
  }

  async function refreshDocuments() {
    const [nextDocuments, nextHealth] = await Promise.all([
      rpc<DocumentRecord[]>("documents.list"),
      rpc<RAGHealth>("rag.health")
    ]);
    setDocuments(nextDocuments);
    setRagHealth(nextHealth);
  }

  async function refreshOllama() {
    setError(null);
    try {
      setOllama(await rpc<OllamaStatus>("models.detect_ollama"));
    } catch (err) {
      setError(readError(err));
    }
  }

  async function refreshOcrStatus() {
    setError(null);
    try {
      setOcrStatus(await rpc<OCRStatus>("ocr.status"));
    } catch (err) {
      setError(readError(err));
    }
  }

  async function saveSettings() {
    setBusy(true);
    setError(null);
    try {
      const next = await rpc<Settings>("settings.set", {
        values: { default_model: modelDraft.trim() || "llama3.2" }
      });
      setSettings(next);
      setModelDraft(String(next.default_model ?? "llama3.2"));
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  }

  async function createSession() {
    setBusy(true);
    setError(null);
    try {
      const session = await rpc<Session>("sessions.create", {
        model: modelDraft.trim() || settings.default_model || "llama3.2"
      });
      const nextSessions = await rpc<Session[]>("sessions.list");
      setSessions(nextSessions);
      setSelectedSessionId(session.id);
      setActiveView("chat");
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteSession(sessionId: string) {
    setBusy(true);
    setError(null);
    try {
      await rpc("sessions.delete", { session_id: sessionId });
      const nextSessions = await rpc<Session[]>("sessions.list");
      setSessions(nextSessions);
      if (selectedSessionId === sessionId) {
        setSelectedSessionId(nextSessions[0]?.id ?? null);
      }
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  }

  async function loadMessages(sessionId: string) {
    setError(null);
    try {
      setMessages(await rpc<Message[]>("sessions.messages", { session_id: sessionId }));
    } catch (err) {
      setError(readError(err));
    }
  }

  async function importDocumentPath(path: string) {
    if (!path) return;
    const unsupported = unsupportedDocumentReason(path);
    if (unsupported) {
      setError(unsupported);
      return;
    }
    setBusy(true);
    setError(null);
    setLastIndexResult(null);
    try {
      const result = await rpc<DocumentImportResult>("documents.import", { path, index: true });
      setLastIndexResult(result.index);
      setLastOcrResult(null);
      setImportPath("");
      await refreshDocuments();
      if (result.document.is_low_text || result.document.index_status === "low_text") {
        setError(formatLowTextOcrMessage(ocrStatus));
      }
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  }

  async function importDocument(event: FormEvent) {
    event.preventDefault();
    await importDocumentPath(importPath.trim());
  }

  async function chooseDocument() {
    setError(null);
    try {
      const selected = await open({
        multiple: false,
        filters: [
          {
            name: "Documents",
            extensions: ["txt", "md", "pdf"]
          }
        ]
      });
      if (typeof selected !== "string") return;
      setImportPath(selected);
      await importDocumentPath(selected);
    } catch (err) {
      setError(readError(err));
    }
  }

  async function runOcr(documentId: string) {
    setBusy(true);
    setError(null);
    setLastOcrResult(null);
    try {
      const result = await rpc<OCRResult>("documents.ocr", { document_id: documentId });
      setLastOcrResult(result);
      setOcrStatus(result.ocr_status);
      if (!result.ocr_status.available || !result.index) {
        setError(result.stats.warning || result.document.ocr_error || result.ocr_status.message);
      }
      await refreshDocuments();
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  }

  async function chooseLegacyFolder() {
    setError(null);
    try {
      const selected = await open({
        directory: true,
        multiple: false
      });
      if (typeof selected !== "string") return;
      setLegacyPath(selected);
    } catch (err) {
      setError(readError(err));
    }
  }

  async function importLegacyFolder(event: FormEvent) {
    event.preventDefault();
    const folder = legacyPath.trim();
    if (!folder) return;
    setBusy(true);
    setError(null);
    setLegacyReport(null);
    try {
      const report = await rpc<LegacyImportReport>("legacy.import", { folder });
      setLegacyReport(report);
      await Promise.all([refreshDocuments(), refreshSessions()]);
      const nextSettings = await rpc<Settings>("settings.get");
      setSettings(nextSettings);
      setModelDraft(String(nextSettings.default_model ?? "llama3.2"));
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  }

  async function refreshSessions() {
    const nextSessions = await rpc<Session[]>("sessions.list");
    setSessions(nextSessions);
    setSelectedSessionId((current) => current ?? nextSessions[0]?.id ?? null);
  }

  async function deleteDocument(documentId: string) {
    setBusy(true);
    setError(null);
    try {
      await rpc("documents.delete", { document_id: documentId });
      await refreshDocuments();
      if (searchQuery.trim()) {
        await testRetrieval();
      }
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  }

  async function reindexDocument(documentId: string) {
    setBusy(true);
    setError(null);
    setLastIndexResult(null);
    try {
      const result = await rpc<RAGIndexResult>("documents.reindex", { document_id: documentId });
      setLastIndexResult(result);
      await refreshDocuments();
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  }

  async function testRetrieval(event?: FormEvent) {
    event?.preventDefault();
    const query = searchQuery.trim();
    if (!query) return;
    setBusy(true);
    setError(null);
    try {
      setSearchResults(await rpc<RAGSearchResult[]>("rag.search", { query, limit: 5 }));
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = draft.trim();
    if (!message) return;
    setBusy(true);
    setError(null);
    setDraft("");
    setRetrievedChunks([]);
    try {
      const result = await rpc<ChatResult>("chat.send", {
        message,
        session_id: selectedSessionId,
        model: modelDraft.trim() || settings.default_model || "llama3.2",
        use_rag: useRag
      });
      setSelectedSessionId(result.session.id);
      setMessages(result.messages);
      setRetrievedChunks(result.retrieved_chunks ?? []);
      setSessions(await rpc<Session[]>("sessions.list"));
    } catch (err) {
      setDraft(message);
      setError(readError(err));
      if (selectedSessionId) {
        await loadMessages(selectedSessionId);
      }
    } finally {
      setBusy(false);
    }
  }

  const hasRuntime = Boolean(ollama?.reachable);

  return (
    <main className="flex h-screen min-h-[620px] bg-paper text-ink">
      <aside className="flex w-[310px] shrink-0 flex-col border-r border-ink/15 bg-[#eeeee6]">
        <div className="border-b border-ink/15 px-4 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-md bg-moss text-white">
              <Bot size={19} aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold">Odysseus Desktop</h1>
              <p className="truncate text-xs text-ink/60">Local AI workspace MVP</p>
            </div>
          </div>
        </div>

        <nav className="grid grid-cols-2 gap-2 border-b border-ink/15 px-4 py-3">
          <button
            className={`rounded-md px-3 py-2 text-sm font-medium ${
              activeView === "chat" ? "bg-moss text-white" : "bg-white hover:bg-[#faf9f3]"
            }`}
            onClick={() => setActiveView("chat")}
            type="button"
          >
            Chat
          </button>
          <button
            className={`rounded-md px-3 py-2 text-sm font-medium ${
              activeView === "documents" ? "bg-moss text-white" : "bg-white hover:bg-[#faf9f3]"
            }`}
            onClick={() => setActiveView("documents")}
            type="button"
          >
            Documents
          </button>
        </nav>

        <section className="border-b border-ink/15 px-4 py-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase text-ink/55">
            <Database size={14} aria-hidden="true" />
            Profile
          </div>
          <p className="truncate text-sm font-medium">{appStatus?.profile_id ?? "default"}</p>
          <p className="mt-1 truncate text-xs text-ink/55" title={appStatus?.profile_dir ?? ""}>
            {appStatus?.profile_dir ?? "Starting..."}
          </p>
        </section>

        <section className="border-b border-ink/15 px-4 py-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase text-ink/55">
              <Power size={14} aria-hidden="true" />
              Ollama
            </div>
            <IconButton onClick={refreshOllama} title="Refresh runtime">
              <RefreshCw size={15} aria-hidden="true" />
            </IconButton>
          </div>
          <RuntimeStatus status={ollama} />
        </section>

        <section className="border-b border-ink/15 px-4 py-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase text-ink/55">
            <SettingsIcon size={14} aria-hidden="true" />
            Settings
          </div>
          <div className="flex gap-2">
            <input
              className="min-w-0 flex-1 rounded-md border border-ink/20 bg-white px-3 py-2 text-sm outline-none focus:border-tide"
              value={modelDraft}
              onChange={(event) => setModelDraft(event.target.value)}
              placeholder="llama3.2"
            />
            <IconButton className="bg-tide text-white hover:bg-[#2e5c66]" disabled={busy} onClick={saveSettings} title="Save settings">
              <Save size={17} aria-hidden="true" />
            </IconButton>
          </div>
        </section>

        <section className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between px-4 py-3">
            <h2 className="text-xs font-semibold uppercase text-ink/55">Sessions</h2>
            <IconButton disabled={busy} onClick={createSession} title="New session">
              <MessageSquarePlus size={15} aria-hidden="true" />
            </IconButton>
          </div>
          <div className="scrollbar-thin min-h-0 flex-1 overflow-auto px-2 pb-3">
            {sessions.length === 0 ? (
              <p className="px-2 py-3 text-sm text-ink/55">No sessions yet.</p>
            ) : (
              sessions.map((session) => (
                <button
                  key={session.id}
                  className={`mb-1 flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm ${
                    selectedSessionId === session.id ? "bg-moss text-white" : "hover:bg-white"
                  }`}
                  onClick={() => {
                    setSelectedSessionId(session.id);
                    setActiveView("chat");
                  }}
                  type="button"
                >
                  <span className="min-w-0 flex-1 truncate">{session.title}</span>
                  <span
                    className={`truncate text-xs ${
                      selectedSessionId === session.id ? "text-white/70" : "text-ink/45"
                    }`}
                  >
                    {session.model || settings.default_model || ""}
                  </span>
                </button>
              ))
            )}
          </div>
        </section>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        {activeView === "chat" ? (
          <ChatWorkspace
            busy={busy}
            draft={draft}
            error={error}
            hasRuntime={hasRuntime}
            loadState={loadState}
            messages={messages}
            retrievedChunks={retrievedChunks}
            selectedSession={selectedSession}
            settings={settings}
            useRag={useRag}
            onDeleteSession={deleteSession}
            onRetry={bootstrap}
            onSend={sendMessage}
            onSetDraft={setDraft}
            onSetUseRag={setUseRag}
          />
        ) : (
          <DocumentsWorkspace
            busy={busy}
            documents={documents}
            error={error}
            importPath={importPath}
            legacyPath={legacyPath}
            legacyReport={legacyReport}
            lastIndexResult={lastIndexResult}
            lastOcrResult={lastOcrResult}
            ocrStatus={ocrStatus}
            ragHealth={ragHealth}
            searchQuery={searchQuery}
            searchResults={searchResults}
            onDelete={deleteDocument}
            onImport={importDocument}
            onChooseDocument={chooseDocument}
            onChooseLegacyFolder={chooseLegacyFolder}
            onImportLegacy={importLegacyFolder}
            onRunOcr={runOcr}
            onReindex={reindexDocument}
            onRefreshOcrStatus={refreshOcrStatus}
            onSearch={testRetrieval}
            onSetImportPath={setImportPath}
            onSetLegacyPath={setLegacyPath}
            onSetSearchQuery={setSearchQuery}
          />
        )}
      </section>
    </main>
  );
}

function ChatWorkspace(props: {
  busy: boolean;
  draft: string;
  error: string | null;
  hasRuntime: boolean;
  loadState: LoadState;
  messages: Message[];
  retrievedChunks: RAGSearchResult[];
  selectedSession: Session | null;
  settings: Settings;
  useRag: boolean;
  onDeleteSession: (sessionId: string) => void;
  onRetry: () => void;
  onSend: (event: FormEvent) => void;
  onSetDraft: (value: string) => void;
  onSetUseRag: (value: boolean) => void;
}) {
  return (
    <>
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-ink/15 px-5">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold">
            {props.selectedSession?.title ?? "New chat"}
          </h2>
          <p className="truncate text-xs text-ink/55">
            {props.selectedSession?.model || props.settings.default_model || "No model selected"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 rounded-md border border-ink/15 bg-white px-3 py-2 text-sm">
            <input
              checked={props.useRag}
              className="h-4 w-4 accent-moss"
              onChange={(event) => props.onSetUseRag(event.target.checked)}
              type="checkbox"
            />
            RAG
          </label>
          {props.selectedSession && (
            <IconButton
              className="text-clay"
              disabled={props.busy}
              onClick={() => props.onDeleteSession(props.selectedSession!.id)}
              title="Delete session"
            >
              <Trash2 size={16} aria-hidden="true" />
            </IconButton>
          )}
        </div>
      </header>

      <ErrorBanner error={props.error} />

      {props.loadState === "error" ? (
        <div className="flex flex-1 items-center justify-center px-6">
          <button
            className="rounded-md bg-moss px-4 py-2 text-sm font-medium text-white"
            onClick={props.onRetry}
            type="button"
          >
            Retry startup
          </button>
        </div>
      ) : (
        <>
          <div className="scrollbar-thin flex-1 overflow-auto px-5 py-5">
            {props.messages.length === 0 ? (
              <div className="flex h-full items-center justify-center text-center">
                <div>
                  <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-md bg-gold/20 text-gold">
                    <Bot size={24} aria-hidden="true" />
                  </div>
                  <p className="text-base font-medium">Ready</p>
                  <p className="mt-1 text-sm text-ink/55">
                    {props.hasRuntime ? "Ollama is reachable." : "Connect Ollama to send a local chat."}
                  </p>
                </div>
              </div>
            ) : (
              <div className="mx-auto flex max-w-3xl flex-col gap-3">
                {props.retrievedChunks.length > 0 && (
                  <RetrievedSources chunks={props.retrievedChunks} />
                )}
                {props.messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
              </div>
            )}
          </div>

          <form
            className="flex shrink-0 gap-3 border-t border-ink/15 bg-[#eeeee6] px-5 py-4"
            onSubmit={props.onSend}
          >
            <textarea
              className="max-h-32 min-h-12 flex-1 resize-none rounded-md border border-ink/20 bg-white px-3 py-3 text-sm outline-none focus:border-tide"
              value={props.draft}
              onChange={(event) => props.onSetDraft(event.target.value)}
              placeholder={props.useRag ? "Ask with document retrieval" : "Message"}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <button
              className="flex h-12 w-12 shrink-0 items-center justify-center rounded-md bg-moss text-white hover:bg-[#35543d]"
              disabled={props.busy || !props.draft.trim()}
              title="Send"
              type="submit"
            >
              <Send size={18} aria-hidden="true" />
            </button>
          </form>
        </>
      )}
    </>
  );
}

function DocumentsWorkspace(props: {
  busy: boolean;
  documents: DocumentRecord[];
  error: string | null;
  importPath: string;
  legacyPath: string;
  legacyReport: LegacyImportReport | null;
  lastIndexResult: RAGIndexResult | null;
  lastOcrResult: OCRResult | null;
  ocrStatus: OCRStatus | null;
  ragHealth: RAGHealth | null;
  searchQuery: string;
  searchResults: RAGSearchResult[];
  onDelete: (documentId: string) => void;
  onChooseDocument: () => void;
  onChooseLegacyFolder: () => void;
  onImport: (event: FormEvent) => void;
  onImportLegacy: (event: FormEvent) => void;
  onReindex: (documentId: string) => void;
  onRefreshOcrStatus: () => void;
  onRunOcr: (documentId: string) => void;
  onSearch: (event?: FormEvent) => void;
  onSetImportPath: (value: string) => void;
  onSetLegacyPath: (value: string) => void;
  onSetSearchQuery: (value: string) => void;
}) {
  return (
    <>
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-ink/15 px-5">
        <div>
          <h2 className="text-lg font-semibold">Documents</h2>
          <p className="text-xs text-ink/55">
            {props.ragHealth
              ? `${props.ragHealth.documents} document(s), ${props.ragHealth.chunks} chunk(s), ${props.ragHealth.cached_embeddings} cached embedding(s)`
              : "Index status unavailable"}
          </p>
        </div>
      </header>

      <ErrorBanner error={props.error} />

      <div className="scrollbar-thin min-h-0 flex-1 overflow-auto px-5 py-5">
        <div className="grid grid-cols-[minmax(280px,380px)_minmax(0,1fr)] gap-5">
          <section className="space-y-4">
            <form className="rounded-md border border-ink/15 bg-white p-4" onSubmit={props.onImport}>
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <Upload size={17} aria-hidden="true" />
                Import
              </div>
              <div className="flex gap-2">
                <input
                  className="min-w-0 flex-1 rounded-md border border-ink/20 px-3 py-2 text-sm outline-none focus:border-tide"
                  onChange={(event) => props.onSetImportPath(event.target.value)}
                  placeholder="C:\\path\\to\\document.txt"
                  value={props.importPath}
                />
                <button
                  className="flex h-10 shrink-0 items-center justify-center gap-2 rounded-md border border-ink/15 bg-white px-3 text-sm font-medium hover:bg-[#faf9f3]"
                  disabled={props.busy}
                  onClick={props.onChooseDocument}
                  type="button"
                >
                  <FileText size={16} aria-hidden="true" />
                  Browse
                </button>
              </div>
              <p className="mt-2 text-xs text-ink/55">Supported now: .txt, .md, extractable .pdf</p>
              <div className="mt-3 rounded-md border border-dashed border-ink/20 bg-[#faf9f3] px-3 py-4 text-center text-xs text-ink/55">
                Drop a supported document here to import it.
              </div>
              <button
                className="mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-moss px-3 text-sm font-medium text-white hover:bg-[#35543d]"
                disabled={props.busy || !props.importPath.trim()}
                type="submit"
              >
                <Upload size={16} aria-hidden="true" />
                Import and index
              </button>
            </form>

            <OCRDebugStatus
              busy={props.busy}
              onRefresh={props.onRefreshOcrStatus}
              status={props.ocrStatus}
            />

            <form className="rounded-md border border-ink/15 bg-white p-4" onSubmit={props.onSearch}>
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <Search size={17} aria-hidden="true" />
                Test Retrieval
              </div>
              <textarea
                className="min-h-20 w-full resize-none rounded-md border border-ink/20 px-3 py-2 text-sm outline-none focus:border-tide"
                onChange={(event) => props.onSetSearchQuery(event.target.value)}
                placeholder="Search the local document index"
                value={props.searchQuery}
              />
              <button
                className="mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-tide px-3 text-sm font-medium text-white hover:bg-[#2e5c66]"
                disabled={props.busy || !props.searchQuery.trim()}
                type="submit"
              >
                <Search size={16} aria-hidden="true" />
                Search chunks
              </button>
            </form>

            {props.lastIndexResult && (
              <div className="rounded-md border border-tide/20 bg-[#eef8f8] p-4 text-sm">
                <p className="font-medium">{props.lastIndexResult.document.title}</p>
                <p className="mt-1 text-xs text-tide">
                  {props.lastIndexResult.chunks.length} chunk(s), {props.lastIndexResult.embedded} embedded,{" "}
                  {props.lastIndexResult.cached} from cache
                </p>
              </div>
            )}

            {props.lastOcrResult && (
              <OCRResultSummary result={props.lastOcrResult} />
            )}

            <form className="rounded-md border border-ink/15 bg-white p-4" onSubmit={props.onImportLegacy}>
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <Database size={17} aria-hidden="true" />
                Legacy Import
              </div>
              <div className="flex gap-2">
                <input
                  className="min-w-0 flex-1 rounded-md border border-ink/20 px-3 py-2 text-sm outline-none focus:border-tide"
                  onChange={(event) => props.onSetLegacyPath(event.target.value)}
                  placeholder="C:\\path\\to\\old\\odysseus"
                  value={props.legacyPath}
                />
                <button
                  className="flex h-10 shrink-0 items-center justify-center gap-2 rounded-md border border-ink/15 bg-white px-3 text-sm font-medium hover:bg-[#faf9f3]"
                  disabled={props.busy}
                  onClick={props.onChooseLegacyFolder}
                  type="button"
                >
                  <FileText size={16} aria-hidden="true" />
                  Folder
                </button>
              </div>
              <p className="mt-2 text-xs text-ink/55">
                Reads old data and copies compatible items into this profile.
              </p>
              <button
                className="mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-gold px-3 text-sm font-medium text-white hover:bg-[#a8772b]"
                disabled={props.busy || !props.legacyPath.trim()}
                type="submit"
              >
                <Upload size={16} aria-hidden="true" />
                Import legacy data
              </button>
            </form>

            {props.legacyReport && <LegacyReportView report={props.legacyReport} />}
          </section>

          <section className="min-w-0 space-y-4">
            <div className="rounded-md border border-ink/15 bg-white">
              <div className="flex items-center gap-2 border-b border-ink/10 px-4 py-3 text-sm font-semibold">
                <FileText size={17} aria-hidden="true" />
                Document List
              </div>
              {props.documents.length === 0 ? (
                <p className="px-4 py-6 text-sm text-ink/55">No documents imported yet.</p>
              ) : (
                <div className="divide-y divide-ink/10">
                  {props.documents.map((document) => (
                    <DocumentRow
                      busy={props.busy}
                      document={document}
                      key={document.id}
                      ocrStatus={props.ocrStatus}
                      onDelete={props.onDelete}
                      onReindex={props.onReindex}
                      onRunOcr={props.onRunOcr}
                    />
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-md border border-ink/15 bg-white">
              <div className="flex items-center gap-2 border-b border-ink/10 px-4 py-3 text-sm font-semibold">
                <Search size={17} aria-hidden="true" />
                Retrieval Results
              </div>
              {props.searchResults.length === 0 ? (
                <p className="px-4 py-6 text-sm text-ink/55">Run a search to inspect retrieved chunks.</p>
              ) : (
                <div className="divide-y divide-ink/10">
                  {props.searchResults.map((result) => (
                    <div className="px-4 py-3" key={result.chunk_id}>
                      <div className="flex items-center justify-between gap-3 text-xs text-ink/55">
                        <span className="truncate">
                          {String(result.metadata.title ?? result.metadata.file_name ?? "Document")}
                          {result.page_start ? `, page ${result.page_start}` : ""}
                        </span>
                        <span>{result.score.toFixed(3)}</span>
                      </div>
                      <p className="mt-2 line-clamp-4 text-sm leading-6">{result.content}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </>
  );
}

const OCR_DEPENDENCIES: Array<[OCRDependencyName, string]> = [
  ["tesseract", "Tesseract"],
  ["pdftoppm", "pdftoppm"],
  ["mutool", "mutool"]
];

function OCRDebugStatus(props: {
  busy: boolean;
  onRefresh: () => void;
  status: OCRStatus | null;
}) {
  const available = Boolean(props.status?.available);
  return (
    <section className="rounded-md border border-ink/15 bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-sm font-semibold">
          {available ? (
            <CheckCircle2 className="text-moss" size={17} aria-hidden="true" />
          ) : (
            <CircleAlert className="text-clay" size={17} aria-hidden="true" />
          )}
          <span>OCR Status</span>
        </div>
        <IconButton disabled={props.busy} onClick={props.onRefresh} title="Refresh OCR status">
          <RefreshCw size={15} aria-hidden="true" />
        </IconButton>
      </div>
      <p className={`text-xs ${available ? "text-moss" : "text-clay"}`}>
        {props.status ? props.status.message : "Checking OCR dependencies..."}
      </p>
      {props.status?.renderer && (
        <p className="mt-1 text-xs text-ink/55">PDF renderer: {props.status.renderer}</p>
      )}
      <div className="mt-3 space-y-2">
        {OCR_DEPENDENCIES.map(([name, label]) => {
          const dependency = props.status?.dependencies?.[name];
          const found = Boolean(dependency?.found);
          return (
            <div className="flex items-start gap-2 rounded-md bg-[#faf9f3] px-2 py-2" key={name}>
              {found ? (
                <CheckCircle2 className="mt-0.5 shrink-0 text-moss" size={14} aria-hidden="true" />
              ) : (
                <CircleAlert className="mt-0.5 shrink-0 text-clay" size={14} aria-hidden="true" />
              )}
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium">
                  {label} {found ? "found" : "missing"}
                </p>
                <p className="truncate text-xs text-ink/55" title={dependency?.path || ""}>
                  {formatDependencyDetail(dependency)}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function OCRResultSummary({ result }: { result: OCRResult }) {
  const ok = Boolean(result.index);
  const warning = result.stats.warning || result.document.ocr_error;
  return (
    <div
      className={`rounded-md border p-4 text-sm ${
        ok ? "border-moss/20 bg-[#edf7ef]" : "border-gold/30 bg-[#fff8e8]"
      }`}
    >
      <div className="flex items-start gap-2">
        {ok ? (
          <CheckCircle2 className="mt-0.5 shrink-0 text-moss" size={16} aria-hidden="true" />
        ) : (
          <AlertTriangle className="mt-0.5 shrink-0 text-[#7a561d]" size={16} aria-hidden="true" />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{result.document.title}</p>
          <p className={`mt-1 text-xs ${ok ? "text-moss" : "text-[#7a561d]"}`}>
            OCR {result.document.ocr_status}
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-ink/65">
            <Metric label="Pages processed" value={result.stats.pages_processed} />
            <Metric label="Pages with text" value={result.stats.pages_with_text} />
            <Metric label="Chunks" value={result.stats.chunks_created} />
            <Metric label="Embeddings new/cache" value={`${result.stats.embeddings_created}/${result.stats.embeddings_cached}`} />
          </div>
          {warning && <p className="mt-3 text-xs text-[#7a561d]">{warning}</p>}
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-md bg-white/70 px-2 py-2">
      <p className="font-medium text-ink">{value}</p>
      <p className="mt-0.5 text-ink/55">{label}</p>
    </div>
  );
}

function DocumentRow(props: {
  busy: boolean;
  document: DocumentRecord;
  ocrStatus: OCRStatus | null;
  onDelete: (documentId: string) => void;
  onReindex: (documentId: string) => void;
  onRunOcr: (documentId: string) => void;
}) {
  const lowText = props.document.is_low_text || props.document.index_status === "low_text";
  const canRunOcr = lowText && Boolean(props.ocrStatus?.available);
  const ocrIndexed = props.document.ocr_status === "indexed";
  return (
    <div className="px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{props.document.title}</p>
          <p className="mt-1 truncate text-xs text-ink/55" title={props.document.source_path}>
            {props.document.file_name} · {formatBytes(props.document.size_bytes)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusPill document={props.document} />
          {canRunOcr && (
            <button
              className="flex h-9 items-center justify-center gap-2 rounded-md border border-moss/20 bg-[#edf7ef] px-3 text-xs font-medium text-moss hover:bg-[#e2f0e5]"
              disabled={props.busy}
              onClick={() => props.onRunOcr(props.document.id)}
              type="button"
            >
              <ScanIcon />
              OCR
            </button>
          )}
          <IconButton
            disabled={props.busy || lowText}
            onClick={() => props.onReindex(props.document.id)}
            title="Reindex document"
          >
            <RotateCw size={15} aria-hidden="true" />
          </IconButton>
          <IconButton
            className="text-clay"
            disabled={props.busy}
            onClick={() => props.onDelete(props.document.id)}
            title="Delete document"
          >
            <Trash2 size={15} aria-hidden="true" />
          </IconButton>
        </div>
      </div>
      {lowText && (
        <div className="mt-3 flex gap-2 rounded-md border border-gold/30 bg-[#fff8e8] px-3 py-2 text-xs text-[#7a561d]">
          <AlertTriangle className="mt-0.5 shrink-0" size={14} aria-hidden="true" />
          <span>
            {props.ocrStatus?.available
              ? `Low-text/scanned PDF detected. OCR is available via ${props.ocrStatus.engine_name}.`
              : props.document.ocr_error || formatOcrUnavailableMessage(props.ocrStatus)}
          </span>
        </div>
      )}
      {ocrIndexed && (
        <div className="mt-3 flex gap-2 rounded-md border border-moss/20 bg-[#edf7ef] px-3 py-2 text-xs text-moss">
          <CheckCircle2 className="mt-0.5 shrink-0" size={14} aria-hidden="true" />
          <span>OCR text indexed with {props.document.ocr_engine || "local OCR"}.</span>
        </div>
      )}
      {props.document.error && !lowText && (
        <p className="mt-2 text-xs text-clay">{props.document.error}</p>
      )}
    </div>
  );
}

function LegacyReportView({ report }: { report: LegacyImportReport }) {
  const sections: Array<[keyof LegacyImportReport, string]> = [
    ["imported", "Imported"],
    ["skipped", "Skipped"],
    ["incompatible", "Incompatible"],
    ["failed", "Failed"]
  ];
  const total = sections.reduce((count, [key]) => count + report[key].length, 0);
  return (
    <div className="rounded-md border border-ink/15 bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-sm font-semibold">Import Report</div>
        <div className="text-xs text-ink/55">{total} item(s)</div>
      </div>
      {report.failed.length > 0 && (
        <div className="mb-3 rounded-md border border-clay/25 bg-[#fff3ee] px-3 py-2 text-xs text-clay">
          Some legacy items could not be imported. The old folder was not changed.
        </div>
      )}
      <div className="space-y-3">
        {sections.map(([key, label]) => (
          <div key={key}>
            <div className={`mb-1 flex items-center justify-between text-xs font-semibold uppercase ${
              key === "failed" && report.failed.length > 0 ? "text-clay" : "text-ink/55"
            }`}>
              <span>{label}</span>
              <span>{report[key].length}</span>
            </div>
            {report[key].length === 0 ? (
              <p className="text-xs text-ink/45">None</p>
            ) : (
              <div className="max-h-32 space-y-1 overflow-auto">
                {report[key].map((item, index) => (
                  <div className="rounded-md bg-[#faf9f3] px-2 py-1 text-xs" key={`${key}-${index}`}>
                    <p className="truncate font-medium">{item.type}: {item.source}</p>
                    <p className="text-ink/55">{item.reason}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function RuntimeStatus({ status }: { status: OllamaStatus | null }) {
  if (!status) {
    return <p className="text-sm text-ink/55">Checking...</p>;
  }

  const ok = status.reachable;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {ok ? (
          <CheckCircle2 className="text-moss" size={16} aria-hidden="true" />
        ) : (
          <CircleAlert className="text-clay" size={16} aria-hidden="true" />
        )}
        <span className="text-sm font-medium">{ok ? "Reachable" : "Not reachable"}</span>
      </div>
      <p className="truncate text-xs text-ink/55" title={status.endpoint}>
        {status.endpoint}
      </p>
      <p className="text-xs text-ink/55">
        {status.models.length > 0 ? `${status.models.length} model(s)` : "No models reported"}
      </p>
      {status.error && <p className="text-xs text-clay">{status.error}</p>}
    </div>
  );
}

function RetrievedSources({ chunks }: { chunks: RAGSearchResult[] }) {
  const sources = summarizeRetrievedSources(chunks);
  return (
    <div className="rounded-md border border-tide/25 bg-[#eef8f8] px-3 py-2 text-xs text-tide">
      <div className="flex items-center gap-2 font-medium">
        <FileText size={14} aria-hidden="true" />
        <span>Retrieved {chunks.length} document chunk(s)</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {sources.map((source) => (
          <span
            className="max-w-full truncate rounded-md border border-tide/20 bg-white px-2 py-1"
            key={source}
            title={source}
          >
            {source}
          </span>
        ))}
      </div>
    </div>
  );
}

function StatusPill({ document }: { document: DocumentRecord }) {
  const lowText = document.is_low_text || document.index_status === "low_text";
  const label = document.ocr_status === "indexed" ? "OCR indexed" : lowText ? "Needs OCR" : document.index_status;
  const classes = lowText
    ? "bg-[#fff8e8] text-[#7a561d] border-gold/30"
    : document.index_status === "indexed"
      ? "bg-[#edf7ef] text-moss border-moss/20"
      : document.index_status === "error"
        ? "bg-[#fff3ee] text-clay border-clay/25"
        : "bg-[#eef8f8] text-tide border-tide/25";
  return (
    <span className={`rounded-md border px-2 py-1 text-xs font-medium ${classes}`}>
      {label}
    </span>
  );
}

function ScanIcon() {
  return <Search size={14} aria-hidden="true" />;
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[82%] whitespace-pre-wrap rounded-md px-3 py-2 text-sm leading-6 ${
          isUser ? "bg-moss text-white" : "border border-ink/15 bg-white text-ink"
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

function IconButton(props: {
  children: React.ReactNode;
  className?: string;
  disabled?: boolean;
  onClick: () => void;
  title: string;
}) {
  return (
    <button
      className={`flex h-9 w-9 items-center justify-center rounded-md border border-ink/15 bg-white hover:bg-[#faf9f3] ${props.className ?? ""}`}
      disabled={props.disabled}
      onClick={props.onClick}
      title={props.title}
      type="button"
    >
      {props.children}
    </button>
  );
}

function ErrorBanner({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="border-b border-clay/30 bg-[#fff3ee] px-5 py-3 text-sm text-clay">
      {error}
    </div>
  );
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function summarizeRetrievedSources(chunks: RAGSearchResult[]): string[] {
  const seen = new Set<string>();
  const sources: string[] = [];
  for (const chunk of chunks) {
    const name = String(chunk.metadata.title ?? chunk.metadata.file_name ?? "Document");
    const page = chunk.page_start ? `p. ${chunk.page_start}` : "";
    const label = page ? `${name}, ${page}` : name;
    if (!seen.has(label)) {
      seen.add(label);
      sources.push(label);
    }
  }
  return sources;
}

function formatLowTextOcrMessage(status: OCRStatus | null): string {
  if (status?.available) {
    const renderer = status.renderer ? ` using ${status.renderer}` : "";
    return `PDF has little/no extractable text. OCR is available via ${status.engine_name}${renderer}.`;
  }
  return formatOcrUnavailableMessage(status);
}

function formatOcrUnavailableMessage(status: OCRStatus | null): string {
  const message = status?.message || "This appears scanned/low-text. OCR is not installed/enabled yet.";
  const missing = missingOcrDependencies(status);
  return missing.length > 0 ? `${message} Missing: ${missing.join(", ")}.` : message;
}

function missingOcrDependencies(status: OCRStatus | null): string[] {
  if (!status?.dependencies) return [];
  const missing: string[] = [];
  if (!status.dependencies.tesseract?.found) {
    missing.push("tesseract");
  }
  if (!status.dependencies.pdftoppm?.found && !status.dependencies.mutool?.found) {
    missing.push("pdftoppm or mutool");
  }
  return missing;
}

function formatDependencyDetail(
  dependency: OCRStatus["dependencies"][OCRDependencyName] | undefined
): string {
  if (!dependency) return "checking";
  if (!dependency.found) return "not found";
  const source = dependency.source === "PATH" ? "PATH" : "fallback";
  return `${source}: ${dependency.path}`;
}

function readError(err: unknown): string {
  if (err instanceof Error) {
    return err.message;
  }
  if (typeof err === "string") {
    return err;
  }
  return "Something went wrong.";
}

function unsupportedDocumentReason(path: string): string | null {
  const lower = path.toLowerCase();
  const supported = SUPPORTED_DOCUMENT_EXTENSIONS.some((extension) => lower.endsWith(extension));
  return supported ? null : "Unsupported file type. Choose a .txt, .md, or extractable .pdf file.";
}

export default App;
