// Typed API contracts for Engineering Missions.
// The runtime module lives at static/js/engineeringMissions.js so the existing
// Odysseus static-module pipeline can load it without a build step.

export type MissionStatus = 'queued' | 'running' | 'completed' | 'failed';

export interface EngineeringMissionStep {
  stage?: string;
  title: string;
  status: MissionStatus | 'queued';
  detail?: string;
  at?: string;
  meta?: Record<string, unknown>;
}

export interface EngineeringMission {
  id: string;
  owner?: string | null;
  kind: 'pr_review' | string;
  status: MissionStatus;
  target_url: string;
  title: string;
  summary?: string | null;
  report_markdown?: string | null;
  payload: Record<string, unknown>;
  audit_log: EngineeringMissionStep[];
  public_report: boolean;
  share_token?: string | null;
  public_url?: string | null;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
  published_at?: string | null;
}

export interface StartPRReviewRequest {
  pr_url: string;
  include_ai: boolean;
}

export interface MissionListResponse {
  items: EngineeringMission[];
}

async function readJson<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message = data?.detail || data?.message || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return data as T;
}

export async function listEngineeringMissions(apiBase = window.location.origin): Promise<EngineeringMission[]> {
  const response = await fetch(`${apiBase}/api/engineering-missions`, { credentials: 'same-origin' });
  return (await readJson<MissionListResponse>(response)).items;
}

export async function getEngineeringMission(id: string, apiBase = window.location.origin): Promise<EngineeringMission> {
  const response = await fetch(`${apiBase}/api/engineering-missions/${encodeURIComponent(id)}`, { credentials: 'same-origin' });
  return readJson<EngineeringMission>(response);
}

export async function publishEngineeringMission(id: string, apiBase = window.location.origin): Promise<EngineeringMission> {
  const response = await fetch(`${apiBase}/api/engineering-missions/${encodeURIComponent(id)}/share`, {
    method: 'POST',
    credentials: 'same-origin',
  });
  return readJson<EngineeringMission>(response);
}

export async function revokeEngineeringMission(id: string, apiBase = window.location.origin): Promise<EngineeringMission> {
  const response = await fetch(`${apiBase}/api/engineering-missions/${encodeURIComponent(id)}/share/revoke`, {
    method: 'POST',
    credentials: 'same-origin',
  });
  return readJson<EngineeringMission>(response);
}

export async function getPublicEngineeringMission(token: string, apiBase = window.location.origin): Promise<EngineeringMission> {
  const response = await fetch(`${apiBase}/api/engineering-missions/public/${encodeURIComponent(token)}`);
  return readJson<EngineeringMission>(response);
}

export async function startPRReview(
  body: StartPRReviewRequest,
  apiBase = window.location.origin,
): Promise<EngineeringMission> {
  const response = await fetch(`${apiBase}/api/engineering-missions/pr-review`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return readJson<EngineeringMission>(response);
}
