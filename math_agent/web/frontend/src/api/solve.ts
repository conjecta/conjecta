import { apiFetch } from '@/api/client';
import type { WsEvent } from '@/types/websocket';

export interface SolveStatus {
  ok?: boolean;
  session_id?: string;
  /** A live server-side task is still running for this session. */
  active?: boolean;
  mode?: string;
  waiting_human?: boolean;
  has_checkpoint?: boolean;
  resumable?: boolean;
}

export function getSolveStatus(sessionId: string): Promise<SolveStatus> {
  return apiFetch(`/api/solve/${encodeURIComponent(sessionId)}/status`);
}

interface SolveTraceResponse {
  ok?: boolean;
  events?: WsEvent[];
}

export function getSolveTrace(sessionId: string): Promise<WsEvent[]> {
  return apiFetch<SolveTraceResponse>(`/api/solve/${encodeURIComponent(sessionId)}/trace`)
    .then((data) => data.events ?? []);
}

export function interruptSolve(sessionId: string): Promise<{ ok?: boolean }> {
  return apiFetch(`/api/solve/${encodeURIComponent(sessionId)}/interrupt`, {
    method: 'POST',
    keepalive: true,
  });
}
