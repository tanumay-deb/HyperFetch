/* Everything this app knows how to ask the server.
 *
 * One module so the shape of every request lives in one place, and so a
 * network failure is a value rather than an exception: `fetch` REJECTS when
 * the host is unreachable rather than returning a failed response, and a
 * caller that forgets to catch that shows a blank screen instead of "can't
 * reach HyperFetch".
 */

export type Status =
  | "Downloading" | "Queued" | "Paused" | "Completed" | "Error" | "Scheduled";

export interface Download {
  id: string;
  name: string;
  status: Status;
  totalBytes: number;
  doneBytes: number;
  percent: number;
  added: number;
  error: string;
  isTorrent: boolean;
  peers: number;
  seeds: number;
  seeding: boolean;
  fetchingMeta: boolean;
  metaFailed: boolean;
  expiresInDays: number | null;
}

export interface Listing {
  downloads: Download[];
  usedBytes: number;
  quotaBytes: number;
  activeCount: number;
  activeLimit: number;
}

export interface User {
  username: string;
  quota: number;
}

export interface SessionInfo {
  enabled: boolean;
  user: User | null;
}

export interface FileEntry {
  index: number;
  name: string;
  path: string;
  size: number;
}

/** status 0 means the request never reached anything. */
export interface Reply<T> {
  ok: boolean;
  status: number;
  body: T & { message?: string; code?: string };
}

async function call<T>(path: string, init?: RequestInit): Promise<Reply<T>> {
  let res: Response;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...init,
    });
  } catch {
    return { ok: false, status: 0, body: {} as never };
  }
  let body: unknown = {};
  try {
    body = await res.json();
  } catch {
    /* an empty body is fine */
  }
  return { ok: res.ok, status: res.status, body: (body ?? {}) as never };
}

export const api = {
  session: () => call<SessionInfo>("/api/session"),

  signIn: (username: string, password: string) =>
    call<{ user: User }>("/api/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  signUp: (username: string, email: string, password: string, code: string) =>
    call<Record<string, never>>("/api/signup", {
      method: "POST",
      body: JSON.stringify({ username, email, password, code }),
    }),

  signOut: () => call<Record<string, never>>("/api/logout", { method: "POST" }),

  list: () => call<Listing>("/api/downloads"),

  add: (url: string) =>
    call<{ id: string; started: boolean }>("/api/downloads", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  pause: (id: string) =>
    call<Record<string, never>>(`/api/downloads/${encodeURIComponent(id)}/pause`, {
      method: "POST",
    }),

  resume: (id: string) =>
    call<Record<string, never>>(`/api/downloads/${encodeURIComponent(id)}/resume`, {
      method: "POST",
    }),

  remove: (id: string) =>
    call<{ filesRemoved: number }>(`/api/downloads/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  files: (id: string) =>
    call<{ ready: boolean; truncated: boolean; files: FileEntry[] }>(
      `/api/downloads/${encodeURIComponent(id)}/files`,
    ),
};

/* Saving is a plain navigation, never fetch(): fetch would pull a
 * multi-gigabyte file into memory before the phone saw a byte of it. The
 * server sends Content-Disposition: attachment, so Safari puts it in Files. */
export function fileHref(id: string, index?: number): string {
  const base = `/api/downloads/${encodeURIComponent(id)}/file`;
  return index === undefined ? base : `${base}/${index}`;
}

export function bytes(n: number): string {
  let v = Number(n) || 0;
  if (v <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 ? v.toFixed(0) : v.toFixed(v < 10 ? 2 : 1)} ${units[i]}`;
}

export function speed(bps: number): string {
  return `${bytes(bps)}/s`;
}

export function eta(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  }
  const h = Math.floor(seconds / 3600);
  return `${h}h ${Math.round((seconds % 3600) / 60)}m`;
}
