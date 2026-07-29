// Centralised fetch helpers so mutation/query code never has to remember to
// check `res.ok` (the most common silent-failure source in this app).

export const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

export class HttpError extends Error {
  status: number;
  body: string;
  constructor(status: number, statusText: string, body: string) {
    super(`HTTP ${status} ${statusText}${body ? `: ${body.slice(0, 240)}` : ''}`);
    this.status = status;
    this.body = body;
  }
}

/**
 * Fetch wrapper that throws on non-2xx and returns parsed JSON.
 *
 * Use everywhere a mutation/query needs to consume a JSON body. Callers that
 * need the raw Response (e.g. file downloads) should keep using `fetch`.
 */
export async function apiJson<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, init);
  if (!res.ok) {
    let body = '';
    try {
      body = await res.text();
    } catch {
      /* ignore */
    }
    throw new HttpError(res.status, res.statusText, body);
  }
  // Some endpoints return 204 / empty body — guard against JSON parse errors.
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new HttpError(res.status, 'Invalid JSON', text);
  }
}
