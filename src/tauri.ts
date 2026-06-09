import { invoke } from "@tauri-apps/api/core";

export type AppStatus = {
  profile_id: string;
  profile_dir: string;
  backend_ready: boolean;
};

export type Settings = {
  default_model?: string;
  ollama_endpoint?: string;
  [key: string]: unknown;
};

export type Session = {
  id: string;
  title: string;
  model: string;
  created_at: number;
  updated_at: number;
  last_message_at: number | null;
};

export type Message = {
  id: string;
  session_id: string;
  role: "system" | "user" | "assistant";
  content: string;
  created_at: number;
};

export type OllamaStatus = {
  name: "ollama";
  installed: boolean;
  reachable: boolean;
  endpoint: string;
  version: string;
  models: string[];
  error: string;
  updated_at: number;
};

export type ChatResult = {
  session: Session;
  user_message: Message;
  assistant_message: Message;
  messages: Message[];
  retrieved_chunks: RAGSearchResult[];
};

export type DocumentRecord = {
  id: string;
  title: string;
  source_path: string;
  stored_path: string;
  file_name: string;
  file_type: string;
  content_hash: string;
  size_bytes: number;
  status: string;
  index_status: string;
  is_deleted: boolean;
  is_low_text: boolean;
  error: string;
  created_at: number;
  updated_at: number;
  indexed_at: number | null;
  ocr_status: string;
  ocr_engine: string;
  ocr_error: string;
};

export type RAGChunk = {
  id: string;
  document_id: string;
  chunk_index: number;
  content: string;
  content_hash: string;
  page_start: number | null;
  page_end: number | null;
  metadata: Record<string, unknown>;
  embedding_model: string;
  embedding_hash: string;
  is_deleted: number;
  created_at: number;
  updated_at: number;
};

export type RAGIndexResult = {
  document: DocumentRecord;
  chunks: RAGChunk[];
  embedded: number;
  cached: number;
  low_text: boolean;
};

export type DocumentImportResult = {
  document: DocumentRecord;
  index: RAGIndexResult | null;
};

export type RAGSearchResult = {
  chunk_id: string;
  document_id: string;
  content: string;
  score: number;
  page_start: number | null;
  page_end: number | null;
  metadata: Record<string, unknown>;
};

export type RAGHealth = {
  ok: boolean;
  version: string;
  documents: number;
  chunks: number;
  cached_embeddings: number;
};

export type OCRDependencyName = "tesseract" | "pdftoppm" | "mutool";

export type OCRDependencyStatus = {
  found: boolean;
  path: string;
  source: string;
};

export type OCRStatus = {
  available: boolean;
  engine_name: string;
  renderer: string;
  message: string;
  dependencies: Record<OCRDependencyName, OCRDependencyStatus>;
};

export type OCRPage = {
  id: string;
  document_id: string;
  source_path: string;
  page_number: number;
  engine_name: string;
  confidence: number | null;
  text: string;
  text_hash: string;
  chunk_ids: string[];
  index_status: string;
  created_at: number;
  updated_at: number;
};

export type OCRStats = {
  pages_processed: number;
  pages_with_text: number;
  chunks_created: number;
  embeddings_created: number;
  embeddings_cached: number;
  warning: string;
};

export type OCRResult = {
  document: DocumentRecord;
  ocr_status: OCRStatus;
  ocr_pages: OCRPage[];
  stats: OCRStats;
  index: RAGIndexResult | null;
};

export type LegacyImportReportItem = {
  type: string;
  source: string;
  reason: string;
};

export type LegacyImportReport = {
  imported: LegacyImportReportItem[];
  skipped: LegacyImportReportItem[];
  incompatible: LegacyImportReportItem[];
  failed: LegacyImportReportItem[];
};

export async function getAppStatus(): Promise<AppStatus> {
  return invoke<AppStatus>("app_status");
}

export async function rpc<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  return invoke<T>("rpc_call", { method, params });
}
