import { apiFetch } from './client';

export interface UsageSummary {
  unlimited_quota?: boolean;
  today: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost_usd: number;
    quota_tokens: number;
    remaining_tokens: number;
  };
  this_month: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost_usd: number;
  };
}

export interface ApiKeyInfo {
  provider: string;
  updated_at: string;
}

export async function fetchUsage(): Promise<UsageSummary> {
  const res = await apiFetch<{ ok: boolean } & UsageSummary>('/api/me/usage');
  return res;
}

export async function fetchApiKey(): Promise<ApiKeyInfo | null> {
  const res = await apiFetch<{ ok: boolean; api_key: ApiKeyInfo | null }>('/api/me/api-key');
  return res.api_key;
}

export async function setApiKey(provider: string, apiKey: string): Promise<ApiKeyInfo> {
  const res = await apiFetch<{ ok: boolean; provider: string; updated_at: string }>('/api/me/api-key', {
    method: 'POST',
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
  return { provider: res.provider, updated_at: res.updated_at };
}

export async function deleteApiKey(): Promise<void> {
  await apiFetch<{ ok: boolean }>('/api/me/api-key', { method: 'DELETE' });
}
