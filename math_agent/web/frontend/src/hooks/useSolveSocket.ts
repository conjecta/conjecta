import { useCallback, useEffect, useRef, useReducer, type Dispatch } from 'react';
import type { HumanDecisionRequest, WsEvent, SolveRequest } from '@/types/websocket';
import { interruptSolve } from '@/api/solve';
import {
  isPublicErrorMessage,
  messageFromErrorResponse,
  publicErrorMessage,
} from '@/lib/publicError';

export type Status =
  | 'idle'
  | 'connecting'
  | 'streaming'
  | 'waiting_human'
  | 'error'
  | 'done'
  | 'interrupted'
  | 'background';

export type SolveMode = 'auto' | 'react' | 'research';

export function finalAnswerFromEvents(events: WsEvent[]): string {
  const done = [...events].reverse().find((e) => e.type === 'done') as any;
  if (!done) return '';
  if (typeof done.final_answer === 'string' && done.final_answer) return done.final_answer;
  return typeof done.summary === 'string' ? done.summary : '';
}

interface State {
  status: Status;
  events: WsEvent[];
  connectionError: string | null;
  backgroundSessionId: string | null;
  sessionId: string | null;
}

type Action =
  | { type: 'connect' }
  | { type: 'event'; payload: WsEvent }
  | { type: 'events'; payload: WsEvent[] }
  | { type: 'error'; message: string }
  | { type: 'done' }
  | { type: 'interrupt' }
  | { type: 'background'; sessionId: string | null }
  | { type: 'replay'; events: WsEvent[] }
  | { type: 'wait_human' }
  | { type: 'clear' };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'connect':
      return {
        ...state,
        status: 'connecting',
        connectionError: null,
        backgroundSessionId: null,
      };
    case 'event':
      return {
        ...state,
        status: state.status === 'connecting' ? 'streaming' : state.status,
        // Stamp client arrival time so the trace can show tool durations.
        events: [...state.events, { ...action.payload, _ts: Date.now() } as WsEvent],
        sessionId:
          action.payload.type === 'session' && 'session_id' in action.payload
            ? String(action.payload.session_id)
            : state.sessionId,
      };
    // Batched sibling of 'event': a coalesced run of token deltas lands in one
    // render instead of one per NDJSON line.
    case 'events': {
      if (action.payload.length === 0) return state;
      const ts = Date.now();
      return {
        ...state,
        status: state.status === 'connecting' ? 'streaming' : state.status,
        events: [
          ...state.events,
          ...action.payload.map((event) => ({ ...event, _ts: ts } as WsEvent)),
        ],
      };
    }
    case 'error':
      return { ...state, status: 'error', connectionError: action.message };
    case 'done':
      return { ...state, status: 'done', backgroundSessionId: null };
    case 'interrupt':
      return { ...state, status: 'interrupted', backgroundSessionId: null };
    case 'background':
      return {
        ...state,
        status: 'background',
        backgroundSessionId: action.sessionId,
        connectionError: null,
      };
    case 'wait_human':
      return { ...state, status: 'waiting_human' };
    case 'replay':
      return { ...state, events: action.events };
    case 'clear':
      return {
        status: 'idle',
        events: [],
        connectionError: null,
        backgroundSessionId: null,
        sessionId: null,
      };
    default:
      return state;
  }
}

/** Coalesces `token` deltas into one dispatch per animation frame.
 *
 * A long reasoning run emits tokens far faster than React can paint, and one
 * dispatch per NDJSON line made the whole trace re-render per token. Tokens
 * are pure appends into `tokenBuffer` (see buildTraceDisplay), so batching a
 * run of them is semantically identical to delivering them one by one.
 *
 * Every non-token event is a barrier: it flushes the pending tokens first, so
 * relative order is preserved and control events stay synchronous. */
class TokenBatcher {
  private pending: WsEvent[] = [];
  private frame: number | null = null;

  constructor(private readonly dispatch: Dispatch<Action>) {}

  push(event: WsEvent): void {
    this.pending.push(event);
    if (this.frame != null) return;
    // rAF keeps flushes aligned with paints; a background tab (where rAF is
    // throttled) still drains via the barrier on the next control event.
    this.frame = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame(() => this.flush())
      : (setTimeout(() => this.flush(), 16) as unknown as number);
  }

  flush(): void {
    if (this.frame != null) {
      if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(this.frame);
      else clearTimeout(this.frame as unknown as ReturnType<typeof setTimeout>);
      this.frame = null;
    }
    if (this.pending.length === 0) return;
    const batch = this.pending;
    this.pending = [];
    this.dispatch({ type: 'events', payload: batch });
  }
}

function handleEvent(
  data: WsEvent,
  dispatch: Dispatch<Action>,
  activeRef: React.MutableRefObject<boolean>,
  batcher?: TokenBatcher,
) {
  if (batcher && data.type === 'token') {
    batcher.push(data);
    return;
  }
  // Barrier: anything that is not a token must observe the tokens before it.
  batcher?.flush();
  dispatch({ type: 'event', payload: data });
  if (data.type === 'error') {
    activeRef.current = false;
    dispatch({
      type: 'error',
      message: publicErrorMessage(),
    });
  }
  if (data.type === 'interrupted') {
    activeRef.current = false;
    dispatch({ type: 'interrupt' });
  }
  if (data.type === 'human_input_required') {
    activeRef.current = false;
    dispatch({ type: 'wait_human' });
  }
  if (data.type === 'done') {
    activeRef.current = false;
    dispatch({ type: 'done' });
  }
}

function modeFromRequest(req: SolveRequest | { mode?: string }): SolveMode {
  const mode = req.mode;
  if (mode === 'research' || mode === 'react' || mode === 'auto') return mode;
  return 'auto';
}

export function useSolveSocket() {
  const [state, dispatch] = useReducer(reducer, {
    status: 'idle',
    events: [],
    connectionError: null,
    backgroundSessionId: null,
    sessionId: null,
  });
  const abortRef = useRef<AbortController | null>(null);
  const activeSessionRef = useRef(false);
  const activeSessionIdRef = useRef<string | null>(null);
  const lastModeRef = useRef<SolveMode>('auto');
  const intentionalStopRef = useRef(false);

  const clear = useCallback(() => {
    activeSessionRef.current = false;
    intentionalStopRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    activeSessionIdRef.current = null;
    intentionalStopRef.current = false;
    dispatch({ type: 'clear' });
  }, []);

  const streamRequest = useCallback((url: string, body: unknown, reset: boolean) => {
    intentionalStopRef.current = false;
    if (reset) clear();
    else {
      activeSessionRef.current = false;
      abortRef.current?.abort();
      abortRef.current = null;
    }
    if (body && typeof body === 'object' && 'mode' in body) {
      lastModeRef.current = modeFromRequest(body as SolveRequest);
    }
    dispatch({ type: 'connect' });
    const controller = new AbortController();
    abortRef.current = controller;
    activeSessionRef.current = true;

    (async () => {
      // Declared outside the try so the abort/network paths can still drain a
      // partial frame of tokens.
      const batcher = new TokenBatcher(dispatch);
      try {
        const res = await fetch(url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (!res.ok) {
          throw new Error(await messageFromErrorResponse(res));
        }
        if (!res.body) throw new Error(publicErrorMessage());

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (activeSessionRef.current) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (!line.trim()) continue;
            try {
              const event = JSON.parse(line) as WsEvent;
              if (event.type === 'session' && 'session_id' in event) {
                activeSessionIdRef.current = String(event.session_id);
              }
              if (event.type === 'done' && 'strategy' in event) {
                const strategy = (event as { strategy?: string }).strategy;
                if (strategy === 'research' || strategy === 'react' || strategy === 'auto') {
                  lastModeRef.current = strategy;
                }
              }
              handleEvent(event, dispatch, activeSessionRef, batcher);
            } catch {
              batcher.flush();
              activeSessionRef.current = false;
              dispatch({ type: 'error', message: publicErrorMessage() });
              return;
            }
          }
        }

        if (buffer.trim()) {
          handleEvent(JSON.parse(buffer) as WsEvent, dispatch, activeSessionRef, batcher);
        }
        // Stream ended: nothing else will arrive to act as a barrier.
        batcher.flush();

        if (activeSessionRef.current) {
          activeSessionRef.current = false;
          if (lastModeRef.current === 'research' && activeSessionIdRef.current) {
            dispatch({ type: 'background', sessionId: activeSessionIdRef.current });
          } else {
            dispatch({ type: 'error', message: publicErrorMessage() });
          }
        }
      } catch (err) {
        batcher.flush();
        if (
          controller.signal.aborted
          || (err instanceof DOMException && err.name === 'AbortError')
        ) {
          activeSessionRef.current = false;
          if (
            !intentionalStopRef.current
            && lastModeRef.current === 'research'
            && activeSessionIdRef.current
          ) {
            dispatch({ type: 'background', sessionId: activeSessionIdRef.current });
          } else {
            dispatch({ type: 'interrupt' });
          }
          return;
        }
        activeSessionRef.current = false;
        dispatch({
          type: 'error',
          message: err instanceof Error && isPublicErrorMessage(err.message)
            ? err.message
            : publicErrorMessage(),
        });
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    })();
  }, [clear]);

  const sendProblem = useCallback((req: SolveRequest) => {
    lastModeRef.current = modeFromRequest(req);
    streamRequest('/api/solve/stream', req, true);
  }, [streamRequest]);

  const submitHumanDecision = useCallback((
    checkpointId: string,
    decision: HumanDecisionRequest,
  ) => {
    streamRequest(
      `/api/solve/${encodeURIComponent(checkpointId)}/decisions/stream`,
      decision,
      false,
    );
  }, [streamRequest]);

  const resumeCheckpoint = useCallback((checkpointId: string) => {
    streamRequest(
      '/api/solve/stream',
      {
        problem: '',
        checkpoint_id: checkpointId,
        mode: lastModeRef.current === 'research' ? 'research' : 'react',
      },
      false,
    );
  }, [streamRequest]);

  const interrupt = useCallback(() => {
    intentionalStopRef.current = true;
    const controller = abortRef.current;
    const sessionId = activeSessionIdRef.current;
    if (!sessionId) {
      controller?.abort();
      return;
    }
    void interruptSolve(sessionId)
      .catch(() => {})
      .finally(() => controller?.abort());
  }, []);

  /** Enter background-polling mode for a session recovered after a page
   * refresh / conversation switch (no local stream attached). */
  const markBackground = useCallback((sessionId: string) => {
    activeSessionIdRef.current = sessionId;
    dispatch({ type: 'background', sessionId });
  }, []);

  /** Replace the local event list with a server-side trace (detached task
   * replay); leaves status and the rest of the state untouched. */
  const replayEvents = useCallback((events: WsEvent[]) => {
    dispatch({ type: 'replay', events });
  }, []);

  useEffect(() => {
    return () => {
      intentionalStopRef.current = true;
      activeSessionRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  return {
    status: state.status,
    events: state.events,
    connectionError: state.connectionError,
    backgroundSessionId: state.backgroundSessionId,
    sessionId: state.sessionId,
    lastMode: lastModeRef.current,
    sendProblem,
    submitHumanDecision,
    resumeCheckpoint,
    interrupt,
    markBackground,
    replayEvents,
    clear,
  };
}
