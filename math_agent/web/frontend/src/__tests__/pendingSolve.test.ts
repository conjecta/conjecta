import { describe, expect, it } from 'vitest';
import { findPendingSolveTurn } from '@/lib/pendingSolve';
import type { ProjectTurn } from '@/types/api';

function turn(partial: Partial<ProjectTurn>): ProjectTurn {
  return { id: 't', problem: 'p', answer: '', ...partial };
}

describe('findPendingSolveTurn', () => {
  it('returns null without a selected conversation', () => {
    const turns = [turn({ conversation_id: 'c1', session_id: 's1' })];
    expect(findPendingSolveTurn(turns, null)).toBeNull();
  });

  it('finds the latest unanswered turn with a session_id in the conversation', () => {
    const pending = turn({
      id: 't2',
      conversation_id: 'c1',
      session_id: 's-live',
      created_at: '2026-07-26T14:00:00Z',
    });
    const turns = [
      turn({ id: 't1', conversation_id: 'c1', answer: 'done', session_id: 's-old' }),
      pending,
    ];
    expect(findPendingSolveTurn(turns, 'c1')).toBe(pending);
  });

  it('ignores other conversations and already-answered latest turns', () => {
    const turns = [
      turn({ id: 't1', conversation_id: 'c2', session_id: 's-other' }),
      turn({ id: 't2', conversation_id: 'c1', answer: 'finished', session_id: 's1' }),
    ];
    expect(findPendingSolveTurn(turns, 'c1')).toBeNull();
  });

  it('returns null when the latest unanswered turn has no session_id', () => {
    const turns = [turn({ id: 't1', conversation_id: 'c1' })];
    expect(findPendingSolveTurn(turns, 'c1')).toBeNull();
  });

  it('returns null for empty or missing turns', () => {
    expect(findPendingSolveTurn([], 'c1')).toBeNull();
    expect(findPendingSolveTurn(undefined, 'c1')).toBeNull();
  });
});
