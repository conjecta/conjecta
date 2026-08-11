// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { deleteApiKey, fetchApiKey, fetchUsage, setApiKey } from '@/api/billing';

function jsonResponse(data: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
  } as Response;
}

describe('billing api', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('fetchUsage returns summary', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      ok: true,
      today: {
        prompt_tokens: 60,
        completion_tokens: 40,
        total_tokens: 100,
        cost_usd: 0.01,
        quota_tokens: 500000,
        remaining_tokens: 499900,
      },
      this_month: {
        prompt_tokens: 60,
        completion_tokens: 40,
        total_tokens: 100,
        cost_usd: 0.01,
      },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const data = await fetchUsage();

    expect(data.today.total_tokens).toBe(100);
    expect(data.this_month.cost_usd).toBe(0.01);
    expect(fetchMock).toHaveBeenCalledWith('/api/me/usage', expect.objectContaining({ credentials: 'same-origin' }));
  });

  it('fetchApiKey returns key info when present', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      ok: true,
      api_key: { provider: 'openai', updated_at: '2026-07-14T10:00:00Z' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const key = await fetchApiKey();

    expect(key).toEqual({ provider: 'openai', updated_at: '2026-07-14T10:00:00Z' });
    expect(fetchMock).toHaveBeenCalledWith('/api/me/api-key', expect.objectContaining({ credentials: 'same-origin' }));
  });

  it('fetchApiKey returns null when no key is set', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, api_key: null }));
    vi.stubGlobal('fetch', fetchMock);

    const key = await fetchApiKey();

    expect(key).toBeNull();
  });

  it('setApiKey posts provider and key', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      ok: true,
      provider: 'anthropic',
      updated_at: '2026-07-14T11:00:00Z',
    }));
    vi.stubGlobal('fetch', fetchMock);

    const key = await setApiKey('anthropic', 'sk-ant-test');

    expect(key).toEqual({ provider: 'anthropic', updated_at: '2026-07-14T11:00:00Z' });
    const [, init] = fetchMock.mock.calls[0];
    expect(init).toEqual(expect.objectContaining({ method: 'POST' }));
    expect(JSON.parse(init.body as string)).toEqual({ provider: 'anthropic', api_key: 'sk-ant-test' });
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
  });

  it('deleteApiKey sends a DELETE request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await deleteApiKey();

    const [, init] = fetchMock.mock.calls[0];
    expect(init).toEqual(expect.objectContaining({ method: 'DELETE' }));
  });
});
