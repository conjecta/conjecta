// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useDeploymentVersion } from '../hooks/useDeploymentVersion';

function makeResponse(version: string) {
  return Promise.resolve({
    ok: true,
    json: async () => ({ version }),
  } as unknown as Response);
}

describe('useDeploymentVersion', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns false initially and true after the server version changes', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementationOnce(() => makeResponse('v1'))
      .mockImplementationOnce(() => makeResponse('v2'));

    const { result } = renderHook(() => useDeploymentVersion(5_000));
    expect(result.current).toBe(false);

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    expect(result.current).toBe(false);

    vi.advanceTimersByTime(5_000);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current).toBe(true));
  });
});
