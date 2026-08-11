// @vitest-environment jsdom
import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useSolveSocket } from '../hooks/useSolveSocket';

function mockStreamResponse(events: unknown[]) {
  const payload = events.map((e) => `${JSON.stringify(e)}\n`).join('');
  const encoder = new TextEncoder();
  const bytes = encoder.encode(payload);
  return {
    ok: true,
    body: {
      getReader: () => {
        let sent = false;
        return {
          read: async () => {
            if (sent) return { done: true, value: undefined };
            sent = true;
            return { done: false, value: bytes };
          },
        };
      },
    },
  };
}

describe('useSolveSocket', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockStreamResponse([
          { type: 'session', session_id: 'abc' },
          { type: 'token', content: 'hi' },
          { type: 'done', summary: 'done' },
        ]),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('streams events from HTTP solve endpoint', async () => {
    const { result } = renderHook(() => useSolveSocket());
    act(() => result.current.sendProblem({ problem: 'test' }));
    await waitFor(() => expect(result.current.events.length).toBeGreaterThan(0));
    expect(result.current.events[0].type).toBe('session');
    await waitFor(() => expect(result.current.status).toBe('done'));
    expect(fetch).toHaveBeenCalledWith(
      '/api/solve/stream',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        headers: expect.not.objectContaining({ Authorization: expect.anything() }),
      }),
    );
  });

  it('sets connectionError on HTTP failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 502, json: async () => ({ detail: 'bad gateway' }) }),
    );
    const { result } = renderHook(() => useSolveSocket());
    act(() => result.current.sendProblem({ problem: 'test' }));
    await waitFor(() => expect(result.current.connectionError).toBe('服务暂时遇到问题，请稍后重试。'));
    expect(result.current.connectionError).not.toContain('gateway');
  });

  it('surfaces DAILY_QUOTA_EXCEEDED as a bind-API-key message', async () => {
    const body = { detail: 'DAILY_QUOTA_EXCEEDED' };
    const response = {
      ok: false,
      status: 429,
      clone() {
        return this;
      },
      json: async () => body,
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
    const { result } = renderHook(() => useSolveSocket());
    act(() => result.current.sendProblem({ problem: 'test', mode: 'research' }));
    await waitFor(() =>
      expect(result.current.connectionError).toBe(
        '今日免费额度已用完，请在「用量与 API Key」中绑定自己的 API Key 后继续使用。',
      ),
    );
  });

  it('abort triggers interrupt', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'));
          });
        }),
      ),
    );
    const { result } = renderHook(() => useSolveSocket());
    act(() => result.current.sendProblem({ problem: 'test' }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    act(() => result.current.interrupt());
    await waitFor(() => expect(result.current.status).toBe('interrupted'));
  });

  it('resumes checkpoints with the last research mode', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(mockStreamResponse([
        { type: 'session', session_id: 'research-session' },
        { type: 'done', summary: 'partial', strategy: 'research' },
      ]))
      .mockResolvedValueOnce(mockStreamResponse([
        { type: 'session', session_id: 'research-session' },
        { type: 'done', summary: 'continued', strategy: 'research' },
      ]));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useSolveSocket());

    act(() => result.current.sendProblem({ problem: 'hard theorem', mode: 'research' }));
    await waitFor(() => expect(result.current.status).toBe('done'));
    act(() => result.current.resumeCheckpoint('research-session'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const resumeBody = JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body));
    expect(resumeBody.mode).toBe('research');
    expect(resumeBody.checkpoint_id).toBe('research-session');
  });

  it('pauses for human input and resumes through the decision endpoint', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(mockStreamResponse([
        { type: 'session', session_id: 'pause-session' },
        {
          type: 'human_input_required',
          checkpoint_id: 'pause-session',
          request_id: 'hitl-1',
          kind: 'plan_review',
          stage: 'planning',
          question: 'Continue?',
          allowed_decisions: ['approve', 'reject'],
          resumable: true,
        },
      ]))
      .mockResolvedValueOnce(mockStreamResponse([
        { type: 'session', session_id: 'resumed-session' },
        { type: 'done', final_answer: 'finished' },
      ]));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useSolveSocket());

    act(() => result.current.sendProblem({ problem: 'test' }));
    await waitFor(() => expect(result.current.status).toBe('waiting_human'));
    act(() => result.current.submitHumanDecision('pause-session', {
      request_id: 'hitl-1',
      decision: 'approve',
    }));
    await waitFor(() => expect(result.current.status).toBe('done'));

    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/solve/pause-session/decisions/stream',
      expect.objectContaining({ body: JSON.stringify({ request_id: 'hitl-1', decision: 'approve' }) }),
    );
    expect(result.current.events.some((event) => event.type === 'human_input_required')).toBe(true);
  });
});

describe('useSolveSocket background recovery', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('markBackground enters background status for a recovered session', () => {
    const { result } = renderHook(() => useSolveSocket());
    act(() => result.current.markBackground('sess-1'));
    expect(result.current.status).toBe('background');
    expect(result.current.backgroundSessionId).toBe('sess-1');
  });

  it('stamps streamed events with client arrival time for tool durations', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockStreamResponse([
          { type: 'session', session_id: 'abc' },
          { type: 'done', summary: 'done' },
        ]),
      ),
    );
    const { result } = renderHook(() => useSolveSocket());
    act(() => result.current.sendProblem({ problem: 'test' }));
    await waitFor(() => expect(result.current.status).toBe('done'));
    expect(result.current.events.length).toBeGreaterThan(0);
    for (const event of result.current.events) {
      expect(typeof (event as { _ts?: unknown })._ts).toBe('number');
    }
  });

  // Tokens are coalesced per animation frame. These guard the two properties
  // that batching must not break: no token is dropped, and control events keep
  // their position relative to the tokens around them.
  it('preserves every token and its order when coalescing a long run', async () => {
    const tokens = Array.from({ length: 200 }, (_, i) => ({
      type: 'token',
      content: `t${i}`,
    }));
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockStreamResponse([
          { type: 'session', session_id: 'abc' },
          ...tokens,
          { type: 'done', summary: 'done' },
        ]),
      ),
    );
    const { result } = renderHook(() => useSolveSocket());
    act(() => result.current.sendProblem({ problem: 'test' }));
    await waitFor(() => expect(result.current.status).toBe('done'));

    const seen = result.current.events
      .filter((e) => e.type === 'token')
      .map((e) => (e as { content: string }).content);
    expect(seen).toEqual(tokens.map((t) => t.content));
  });

  it('flushes pending tokens before a non-token event', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockStreamResponse([
          { type: 'token', content: 'a' },
          { type: 'token', content: 'b' },
          { type: 'step', step_num: 1, action: 'think' },
          { type: 'token', content: 'c' },
          { type: 'done', summary: 'done' },
        ]),
      ),
    );
    const { result } = renderHook(() => useSolveSocket());
    act(() => result.current.sendProblem({ problem: 'test' }));
    await waitFor(() => expect(result.current.status).toBe('done'));

    const kinds = result.current.events.map((e) =>
      e.type === 'token' ? (e as { content: string }).content : e.type,
    );
    expect(kinds).toEqual(['a', 'b', 'step', 'c', 'done']);
  });
});
