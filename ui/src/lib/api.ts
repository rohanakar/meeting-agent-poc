// Types mirror meeting_agent/api/schemas.py.

export type MeetingStatus =
  | "scheduled"
  | "joining"
  | "waiting"
  | "in_meeting"
  | "transcribing"
  | "processing"
  | "done"
  | "failed"
  | "cancelled";

export const TERMINAL_STATUSES: MeetingStatus[] = ["done", "failed", "cancelled"];

export interface Meeting {
  id: string;
  namespace: string;
  platform: string;
  meeting_url: string;
  title: string;
  status: MeetingStatus;
  join_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  egress_mode: string;
  identity: string;
  exit_ip: string | null;
  admission: string | null;
  failure_reason: string | null;
  metadata: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface MeetingList {
  items: Meeting[];
  total: number;
  limit: number;
  offset: number;
}

export interface BotEvent {
  at: string;
  kind: string;
  detail: Record<string, unknown>;
}

export interface Segment {
  idx: number;
  speaker_label: string;
  speaker_name: string | null;
  start_ms: number;
  end_ms: number;
  text: string;
}

export interface MeetingDetail {
  meeting: Meeting;
  events: BotEvent[];
  segments: Segment[];
  summary: { summary: string; key_points: string[]; decisions: string[]; model: string } | null;
  action_items: { text: string; owner: string | null; due_date: string | null; segment_ids: number[] }[];
}

export interface SearchHit {
  chunk_id: number;
  meeting_id: string;
  meeting_title: string;
  platform: string;
  kind: string;
  text: string;
  start_ms: number | null;
  end_ms: number | null;
  score: number;
  created_at: string;
}

export interface Connection {
  apiKey: string;
  namespace: string;
}

const STORAGE_KEY = "meeting-agent.connection";

export function loadConnection(): Connection {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return JSON.parse(saved) as Connection;
  } catch {
    // Storage can be blocked; the defaults still work against a local API.
  }
  return { apiKey: "dev-api-key", namespace: "default" };
}

export function saveConnection(connection: Connection) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(connection));
  } catch {
    // Not saved, but the page keeps working for this visit.
  }
}

type Params = Record<string, string | number | undefined | null>;

export async function request<T>(
  connection: Connection,
  path: string,
  options: { method?: string; params?: Params; body?: unknown } = {},
): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries({ namespace: connection.namespace, ...options.params })) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  }
  const response = await fetch(`${path}?${query}`, {
    method: options.method ?? "GET",
    headers: {
      "X-API-Key": connection.apiKey,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    const detail = typeof problem.detail === "string" ? problem.detail : JSON.stringify(problem.detail);
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return (response.headers.get("content-type")?.includes("json")
    ? response.json()
    : response.blob()) as Promise<T>;
}
