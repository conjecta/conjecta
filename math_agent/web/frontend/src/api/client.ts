import { messageFromStatusAndDetail, publicErrorMessage } from '@/lib/publicError';

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  headers.delete('Authorization');
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const res = await fetch(path, { credentials: 'same-origin', ...init, headers });
  const text = await res.text();
  let json: unknown;
  try {
    json = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(publicErrorMessage(res.status));
  }

  if (!res.ok) {
    // Never render provider, relay, credential, or infrastructure details from
    // an API response. The full exception remains available in server logs.
    const detail =
      typeof json === 'object' && json !== null && 'detail' in json
        ? (json as { detail?: unknown }).detail
        : undefined;
    throw new Error(messageFromStatusAndDetail(res.status, detail));
  }
  return json as T;
}

export function buildQuery(params: Record<string, string | number | boolean | undefined>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== '') sp.set(k, String(v));
  });
  const q = sp.toString();
  return q ? `?${q}` : '';
}
