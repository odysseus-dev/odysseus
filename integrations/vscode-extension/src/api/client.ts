import * as vscode from "vscode";
import type { ModelOptionDefinition } from "../chat/protocol";

export const DEFAULT_SERVER_URL = "http://127.0.0.1:7860";
export const API_TOKEN_SECRET_KEY = "odysseus.apiToken";

export interface ServerConfig {
  serverUrl: string;
  token: string | null;
}

export interface OdysseusModelCatalogResponse {
  hosts: unknown[];
  items: OdysseusModelCatalogItem[];
}

export interface OdysseusModelCatalogItem {
  endpoint_id?: string;
  endpoint_name?: string;
  url: string;
  models: string[];
  models_display?: string[];
  models_extra?: string[];
  models_extra_display?: string[];
  category?: string;
  endpoint_kind?: string;
  model_type?: string;
  offline?: boolean;
  model_options?: Record<string, ModelOptionDefinition[]>;
}

export interface OdysseusSessionSummary {
  id: string;
  name: string;
  model?: string;
  last_message_at?: string | null;
  updated_at?: string | null;
}

export interface OdysseusHistoryResponse {
  history: Array<{
    role: string;
    content: string;
    metadata?: Record<string, unknown>;
  }>;
}

export interface OdysseusCapabilitiesResponse {
  integration: string;
  token_scopes: string[];
  tools: Record<string, unknown>;
}

export interface OdysseusVersionResponse {
  version: string;
}

export interface ConnectionProbeResult {
  version: string | null;
  capabilities: OdysseusCapabilitiesResponse;
}

export interface OdysseusUploadMeta {
  id: string;
  name: string;
  mime: string;
  size: number;
  uploaded_at: string;
  width?: number | null;
  height?: number | null;
  is_duplicate?: boolean;
}

export interface OdysseusMemoryAddResult {
  ok: boolean;
  count?: number;
  message?: string;
}

export type OdysseusRequestErrorKind = "missing_token" | "auth" | "network" | "server" | "http";

export class OdysseusRequestError extends Error {
  public constructor(
    public readonly kind: OdysseusRequestErrorKind,
    message: string,
    public readonly statusCode?: number,
  ) {
    super(message);
    this.name = "OdysseusRequestError";
  }
}

export class OdysseusApiClient {
  public constructor(private readonly config: ServerConfig) {}

  public async getCapabilities(): Promise<OdysseusCapabilitiesResponse> {
    return this.request<OdysseusCapabilitiesResponse>("/api/codex/capabilities");
  }

  public async getModels(): Promise<OdysseusModelCatalogResponse> {
    return this.request<OdysseusModelCatalogResponse>("/api/models");
  }

  public async getSessions(): Promise<OdysseusSessionSummary[]> {
    return this.request<OdysseusSessionSummary[]>("/api/sessions");
  }

  public async getHistory(sessionId: string): Promise<OdysseusHistoryResponse> {
    return this.request<OdysseusHistoryResponse>(`/api/history/${encodeURIComponent(sessionId)}`);
  }

  public async createSession(input: {
    name?: string;
    endpointId: string;
    endpointUrl: string;
    modelId: string;
  }): Promise<OdysseusSessionSummary> {
    const body = new URLSearchParams();
    body.set("name", input.name ?? "");
    body.set("endpoint_id", input.endpointId);
    body.set("endpoint_url", input.endpointUrl);
    body.set("model", input.modelId);
    body.set("skip_validation", "false");
    return this.request<OdysseusSessionSummary>("/api/session", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: body.toString(),
    });
  }

  public async updateSessionModel(input: {
    sessionId: string;
    endpointId: string;
    endpointUrl: string;
    modelId: string;
  }): Promise<void> {
    const body = new URLSearchParams();
    body.set("endpoint_id", input.endpointId);
    body.set("endpoint_url", input.endpointUrl);
    body.set("model", input.modelId);
    await this.request(`/api/session/${encodeURIComponent(input.sessionId)}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: body.toString(),
    });
  }

  public async stopChat(sessionId: string): Promise<{ stopped: boolean }> {
    return this.request<{ stopped: boolean }>(`/api/chat/stop/${encodeURIComponent(sessionId)}`, {
      method: "POST",
    });
  }

  /** Resolve an "Ask before edits" approval (Agent mode). */
  public async approveAgent(approvalId: string, decision: "approve" | "deny"): Promise<void> {
    const body = new URLSearchParams();
    body.set("approval_id", approvalId);
    body.set("decision", decision);
    await this.request("/api/agent/approve", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
  }

  /**
   * Save a memory to the Brain via the scope-gated Codex memory endpoint.
   * Uses `/api/codex/memory` (not the raw cookie-only `/api/memory/add`) so a
   * bearer API token with the `memory:write` scope is attributed to its real
   * owner. Passing `sessionId` links the memory to the conversation so it
   * clusters with that session in the neural graph.
   */
  public async addMemory(input: {
    text: string;
    category?: string;
    source?: string;
    sessionId?: string | null;
  }): Promise<OdysseusMemoryAddResult> {
    const payload: Record<string, unknown> = {
      text: input.text,
      category: input.category ?? "fact",
      source: input.source ?? "vscode",
    };
    if (input.sessionId) {
      payload.session_id = input.sessionId;
    }
    return this.request<OdysseusMemoryAddResult>("/api/codex/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  public async uploadFiles(
    files: Array<{ name: string; content: Uint8Array; mimeType?: string | null }>,
  ): Promise<OdysseusUploadMeta[]> {
    const body = new FormData();
    for (const file of files) {
      const content = file.content.slice();
      body.append(
        "files",
        new Blob([content], { type: file.mimeType || "application/octet-stream" }),
        file.name,
      );
    }
    const response = await this.request<{ files?: OdysseusUploadMeta[] }>("/api/upload", {
      method: "POST",
      body,
    });
    return response.files ?? [];
  }

  public async getVersion(): Promise<OdysseusVersionResponse> {
    return this.requestWithoutAuth<OdysseusVersionResponse>("/api/version");
  }

  public async probeConnection(): Promise<ConnectionProbeResult> {
    const [version, capabilities] = await Promise.all([
      this.getVersion()
        .then((response) => response.version)
        .catch(() => null),
      this.getCapabilities(),
    ]);

    return { version, capabilities };
  }

  public async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    if (!this.config.token) {
      throw new OdysseusRequestError("missing_token", "Odysseus API token is not configured.");
    }

    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${this.config.token}`);

    return this.performRequest<T>(path, {
      ...init,
      headers,
    });
  }

  public async requestRaw(path: string, init: RequestInit = {}): Promise<Response> {
    if (!this.config.token) {
      throw new OdysseusRequestError("missing_token", "Odysseus API token is not configured.");
    }

    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${this.config.token}`);

    return this.performRawRequest(path, {
      ...init,
      headers,
    });
  }

  public async requestWithoutAuth<T>(path: string, init: RequestInit = {}): Promise<T> {
    return this.performRequest<T>(path, init);
  }

  private async performRequest<T>(path: string, init: RequestInit): Promise<T> {
    const response = await this.performRawRequest(path, init);
    return response.json() as Promise<T>;
  }

  private async performRawRequest(path: string, init: RequestInit): Promise<Response> {
    const url = new URL(path, this.config.serverUrl);
    let response: Response;

    try {
      response = await fetch(url, init);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new OdysseusRequestError("network", `Could not reach ${url.origin}: ${detail}`);
    }

    if (!response.ok) {
      throw await buildRequestError(response);
    }

    return response;
  }
}

export async function readServerConfig(context: vscode.ExtensionContext): Promise<ServerConfig> {
  const token = await context.secrets.get(API_TOKEN_SECRET_KEY);
  const serverUrl = getConfiguredServerUrl();
  return { serverUrl, token: token ?? null };
}

export async function storeServerConfig(
  context: vscode.ExtensionContext,
  serverUrl: string,
  token: string,
): Promise<void> {
  const normalizedUrl = normalizeServerUrl(serverUrl);
  await vscode.workspace
    .getConfiguration("odysseus")
    .update("serverUrl", normalizedUrl, vscode.ConfigurationTarget.Global);
  await context.secrets.store(API_TOKEN_SECRET_KEY, token.trim());
}

export function normalizeServerUrl(value: string): string {
  const raw = value.trim() || DEFAULT_SERVER_URL;
  const url = new URL(raw);
  url.pathname = "/";
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

export function getConfiguredServerUrl(): string {
  const rawServerUrl = vscode.workspace
    .getConfiguration("odysseus")
    .get<string>("serverUrl", DEFAULT_SERVER_URL);
  return safeNormalizeServerUrl(rawServerUrl);
}

function safeNormalizeServerUrl(value: string): string {
  try {
    return normalizeServerUrl(value);
  } catch {
    return DEFAULT_SERVER_URL;
  }
}

async function safeReadText(response: Response): Promise<string> {
  try {
    return (await response.text()).trim();
  } catch {
    return "";
  }
}

async function buildRequestError(response: Response): Promise<OdysseusRequestError> {
  const detail = await safeReadText(response);
  const message = `${response.status} ${response.statusText}${detail ? `: ${detail}` : ""}`;

  if (response.status === 401 || response.status === 403) {
    return new OdysseusRequestError("auth", message, response.status);
  }

  if (response.status >= 500) {
    return new OdysseusRequestError("server", message, response.status);
  }

  return new OdysseusRequestError("http", message, response.status);
}
