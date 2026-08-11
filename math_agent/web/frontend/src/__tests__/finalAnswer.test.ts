import { describe, it, expect } from 'vitest';
import { finalAnswerFromEvents } from '@/hooks/useSolveSocket';

describe('finalAnswerFromEvents', () => {
  it('uses the terminal done.summary and ignores per-step token JSON (realistic stream)', () => {
    const events = [
      { type: 'llm_start', label: 'Generating the next action...' },
      { type: 'token', content: '{"thought": "Let me try substitution.", ' },
      { type: 'token', content: '"action": {"tool": "sympy_eval", "args": {"expr": "x**2 - 4"}}}' },
      { type: 'tool_done', step_num: 1 },
      { type: 'llm_start', label: 'Generating the next action...' },
      { type: 'token', content: '{"thought": "That confirms the roots.", ' },
      { type: 'token', content: '"action": {"tool": "final_answer", "args": {"answer": "x = 2 or x = -2"}}}' },
      { type: 'done', summary: 'x = 2 or x = -2', lean_proofs: [] },
    ] as any;
    expect(finalAnswerFromEvents(events)).toBe('x = 2 or x = -2');
  });

  it('prefers final_answer over summary when both are present (server fallback done event)', () => {
    const events = [
      { type: 'token', content: '{"thought": "..."}' },
      {
        type: 'done',
        summary: '4',
        final_answer: '4',
        lean_proofs: [],
        strategy: 'auto',
      },
    ] as any;
    expect(finalAnswerFromEvents(events)).toBe('4');
  });

  it('returns empty string when there is no done event', () => {
    const events = [
      { type: 'token', content: '{"thought": "still working"}' },
    ] as any;
    expect(finalAnswerFromEvents(events)).toBe('');
  });

  it('returns empty string when done event has neither final_answer nor summary', () => {
    const events = [{ type: 'done' }] as any;
    expect(finalAnswerFromEvents(events)).toBe('');
  });
});
