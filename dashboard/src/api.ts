export type Role = "admin" | "member";
export interface User {
  id: number | string;
  email: string;
  name: string;
  role: Role;
}
export interface AuthResponse {
  user: User;
  csrf_token: string;
}
export interface Health {
  status: string;
  chat_configured: boolean;
  store_url: string;
  gateway_url: string;
}
export interface Tool {
  name: string;
  description?: string;
  inputSchema: Record<string, unknown>;
  permission?: string;
}
export interface Server {
  id: number | string;
  name: string;
  slug: string;
  url: string;
  enabled: boolean;
  status: string;
  last_error?: string;
  last_checked_at?: string;
  auth_type?: string;
  header_name?: string;
  tools: Tool[];
}
export interface Member extends User {
  created_at: string;
  active: boolean;
}
export interface ApiKey {
  id: number | string;
  name: string;
  prefix: string;
  user_id: number | string;
  created_at: string;
  last_used_at?: string;
  revoked_at?: string;
}
export interface Invite {
  id: number | string;
  email: string;
  role: Role;
  expires_at: string;
  accepted_at?: string;
}
export interface Audit {
  id: number | string;
  user_id: number | string;
  user_name: string;
  user_email: string;
  server_name: string;
  tool_name: string;
  timestamp: string;
  duration_ms: number;
  outcome: string;
  error?: string;
  request_id: string;
}
export type AuditWindow = "1h" | "24h" | "7d";
export interface AuditMetrics {
  calls: number;
  completed_calls: number;
  failed_calls: number;
  failure_rate: number | null;
  latency_samples: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  outcomes: Record<string, number>;
}
export interface AuditSummary {
  window: AuditWindow;
  from: string;
  to: string;
  scope: "team" | "self";
  user_id: number | string | null;
  server_id: number | string | null;
  totals: AuditMetrics;
  servers: (AuditMetrics & {
    server_id: number | string | null;
    server_name: string;
    server_deleted: boolean;
  })[];
}
export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result: unknown;
  is_error: boolean;
  duration_ms: number;
}
export interface Confirmation {
  id: string;
  summary: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}
export interface ChatResponse {
  message: string;
  tool_calls: ToolCall[];
  confirmation?: Confirmation;
}
export interface Message {
  role: "user" | "assistant";
  content: string;
  tool_calls?: ToolCall[];
}

export function chatContext(messages: Message[]) {
  const context: { role: Message["role"]; content: string }[] = [];
  let length = 0;
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index];
    if (!message.content.trim()) continue;
    const content = message.content.slice(0, 16000);
    if (context.length === 30 || length + content.length > 40000) break;
    context.unshift({ role: message.role, content });
    length += content.length;
  }
  while (context[0]?.role === "assistant") context.shift();
  return context;
}

let csrf = "";
let sessionVersion = 0;
export function setCsrf(value: string) {
  if (csrf !== value) sessionVersion++;
  csrf = value;
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const requestSession = sessionVersion;
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "include",
    signal: options.signal
      ? AbortSignal.any([
          options.signal,
          AbortSignal.timeout(path.startsWith("/chat") ? 180000 : 30000),
        ])
      : AbortSignal.timeout(path.startsWith("/chat") ? 180000 : 30000),
    headers: {
      ...(options.body !== undefined
        ? { "Content-Type": "application/json" }
        : {}),
      ...(method !== "GET" && csrf ? { "X-CSRF-Token": csrf } : {}),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status}). Please try again.`;
    try {
      const json = await response.json();
      if (typeof json.detail === "string") detail = json.detail;
      else if (Array.isArray(json.detail))
        detail = json.detail.map((v: { msg: string }) => v.msg).join(". ");
    } catch {
      /* Preserve a safe HTTP error for non-JSON responses. */
    }
    if (
      response.status === 401 &&
      !path.startsWith("/auth/") &&
      requestSession === sessionVersion
    )
      window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}
export function errorMessage(error: unknown) {
  if (error instanceof DOMException && error.name === "TimeoutError")
    return "The request timed out. Check your connection and try again.";
  if (error instanceof TypeError)
    return "Cannot reach the workspace. Check your connection and try again.";
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}
export function date(value?: string, full = false) {
  if (!value) return "Not yet";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "Unavailable";
  return new Intl.DateTimeFormat(
    undefined,
    full
      ? { dateStyle: "medium", timeStyle: "short" }
      : { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" },
  ).format(d);
}
export function healthy(server: Server) {
  return (
    server.enabled &&
    ["healthy", "online", "connected", "ready", "ok"].includes(
      server.status.toLowerCase(),
    )
  );
}
