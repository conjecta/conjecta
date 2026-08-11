import type { ProjectTurn } from '@/types/api';

/** Latest turn of the conversation that may still be solving server-side:
 * it has no answer yet but carries a session_id we can poll. Only the most
 * recent turn of the conversation is considered — older unfinished turns are
 * stale (interrupted or superseded). */
export function findPendingSolveTurn(
  turns: ProjectTurn[] | undefined,
  conversationId: string | null,
): ProjectTurn | null {
  if (!conversationId || !turns?.length) return null;
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (turn.conversation_id !== conversationId) continue;
    if (turn.answer && turn.answer.trim()) return null;
    return turn.session_id ? turn : null;
  }
  return null;
}
