// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fetchMe,
  verifyLoginCode,
} from '../api/auth';
import { apiFetch } from '../api/client';

function jsonResponse(data: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
  } as Response;
}

describe('cookie-only phone authentication', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('does not persist the access token returned by code verification', async () => {
    const setItem = vi.spyOn(localStorage, 'setItem');
    const removeItem = vi.spyOn(localStorage, 'removeItem');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ access_token: 'phone-secret' }));
    vi.stubGlobal('fetch', fetchMock);

    await verifyLoginCode('+8613800000000', '123456', 'out-1');

    expect(setItem).not.toHaveBeenCalledWith('conjecta-access-token', expect.anything());
    expect(removeItem).not.toHaveBeenCalledWith('conjecta-access-token');
    const [, init] = fetchMock.mock.calls[0];
    expect(init).toEqual(expect.objectContaining({ credentials: 'same-origin' }));
    expect(new Headers(init.headers).has('Authorization')).toBe(false);
  });

  it('fetches the current user without reading or sending a legacy access token', async () => {
    localStorage.setItem('conjecta-access-token', 'legacy-phone-secret');
    const getItem = vi.spyOn(localStorage, 'getItem');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      user: { id: 'user-1', phone: '+8613800000000' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchMe()).resolves.toEqual({
      user: { id: 'user-1', phone: '+8613800000000' },
      banned: false,
      banMessage: null,
    });

    expect(getItem).not.toHaveBeenCalledWith('conjecta-access-token');
    const [, init] = fetchMock.mock.calls[0];
    expect(init).toEqual(expect.objectContaining({ credentials: 'same-origin' }));
    expect(new Headers(init.headers).has('Authorization')).toBe(false);
  });

  it('uses the session cookie for API requests and ignores legacy phone tokens', async () => {
    localStorage.setItem('conjecta-access-token', 'legacy-phone-secret');
    const getItem = vi.spyOn(localStorage, 'getItem');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await apiFetch('/api/projects', {
      headers: { Authorization: 'Bearer should-not-be-forwarded' },
    });

    expect(getItem).not.toHaveBeenCalledWith('conjecta-access-token');
    const [, init] = fetchMock.mock.calls[0];
    expect(init).toEqual(expect.objectContaining({ credentials: 'same-origin' }));
    expect(new Headers(init.headers).has('Authorization')).toBe(false);
  });

});
