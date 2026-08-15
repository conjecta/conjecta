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
  base_url: string | null;
  model: string;
  requires_rebind: boolean;
  updated_at: string | null;
}

export async function fetchUsage(): Promise<UsageSummary> {
  const res = await apiFetch<{ ok: boolean } & UsageSummary>('/api/me/usage');
  return res;
}

export async function fetchApiKey(): Promise<ApiKeyInfo | null> {
  const res = await apiFetch<{ ok: boolean; api_key: ApiKeyInfo | null }>('/api/me/api-key');
  return res.api_key;
}

export async function setApiKey(baseUrl: string, apiKey: string): Promise<ApiKeyInfo> {
  const res = await apiFetch<{ ok: boolean } & ApiKeyInfo>('/api/me/api-key', {
    method: 'POST',
    body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
  });
  return {
    base_url: res.base_url,
    model: res.model,
    requires_rebind: res.requires_rebind,
    updated_at: res.updated_at,
  };
}

export async function deleteApiKey(): Promise<void> {
  await apiFetch<{ ok: boolean }>('/api/me/api-key', { method: 'DELETE' });
}
