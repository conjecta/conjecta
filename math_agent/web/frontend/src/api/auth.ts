const OUT_ID_KEY = 'conjecta-sms-out-id';

export interface AuthConfig {
  ok: boolean;
  phone_auth_enabled: boolean;
  sms_configured: boolean;
  cloud_storage_configured?: boolean;
}

export interface AuthUser {
  id: string;
  phone: string;
  is_admin?: boolean;
  banned?: boolean;
}

export interface MeResponse {
  user: AuthUser | null;
  banned: boolean;
  banMessage: string | null;
}

export function setSmsOutId(outId: string | null): void {
  if (!outId) localStorage.removeItem(OUT_ID_KEY);
  else localStorage.setItem(OUT_ID_KEY, outId);
}

export function getSmsOutId(): string | null {
  return localStorage.getItem(OUT_ID_KEY);
}

const fetchOpts: RequestInit = { credentials: 'same-origin' };

function detailMessage(data: unknown, status: number): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
  }
  if (data && typeof data === 'object' && 'message' in data) {
    const message = (data as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return `HTTP ${status}`;
}

export async function fetchAuthConfig(): Promise<AuthConfig> {
  const res = await fetch('/api/auth/config', fetchOpts);
  const data = (await res.json()) as AuthConfig;
  return data;
}

export async function sendLoginCode(
  phone: string,
): Promise<{ out_id?: string; sms_bypass?: boolean; access_token?: string }> {
  const res = await fetch('/api/auth/send-code', {
    ...fetchOpts,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ phone }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(detailMessage(data, res.status));
  return data;
}

export async function verifyLoginCode(phone: string, code: string, outId?: string | null): Promise<void> {
  const res = await fetch('/api/auth/verify-code', {
    ...fetchOpts,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ phone, code, out_id: outId || undefined }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(detailMessage(data, res.status));
}

export async function fetchMe(): Promise<MeResponse> {
  const res = await fetch('/api/auth/me', {
    ...fetchOpts,
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    return { user: null, banned: false, banMessage: null };
  }
  const data = await res.json() as {
    user?: AuthUser;
    banned?: boolean;
    ban_message?: string;
  };
  const banned = Boolean(data.banned || data.user?.banned);
  return {
    user: data.user || null,
    banned,
    banMessage: banned ? (data.ban_message || '您的账号已被禁止使用。') : null,
  };
}

export async function logout(): Promise<void> {
  await fetch('/api/auth/logout', { ...fetchOpts, method: 'POST' });
  setSmsOutId(null);
}
