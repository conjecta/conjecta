// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  clearUserProfile,
  deleteUserMemory,
  fetchUserMemories,
  updateUserMemory,
} from '@/api/memories';

function jsonResponse(data: unknown) {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(data),
  } as Response;
}

describe('user memory api', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('lists memories and profile', async () => {
    const payload = {
      ok: true,
      memories: [{ id: 'um-1', content: '用中文回答' }],
      profile: { summary: 'prefers concise answers', version: 2, generated_at: '2026-07-14' },
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchUserMemories();

    expect(result).toEqual({ memories: payload.memories, profile: payload.profile });
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/me/memories',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });

  it('updates and deletes a memory using an encoded id', async () => {
    const memory = {
      id: 'um/a',
      kind: 'preference',
      content: '用中文回答',
      why: '',
      weight: 0.9,
      status: 'snoozed',
      scope: 'global',
      created_at: '',
      updated_at: '',
    } as const;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ ok: true, memory }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    expect(await updateUserMemory('um/a', { status: 'snoozed' })).toEqual(memory);
    await deleteUserMemory('um/a');

    expect(fetchMock.mock.calls[0][0]).toBe('/api/me/memories/um%2Fa');
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: 'PATCH' }));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ status: 'snoozed' });
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({ method: 'DELETE' }));
  });

  it('clears the user profile', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await clearUserProfile();

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/me/memories/profile',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });
});
