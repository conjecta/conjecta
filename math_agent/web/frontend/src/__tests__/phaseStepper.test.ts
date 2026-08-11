import { describe, expect, it } from 'vitest';
import { derivePhaseStepper } from '@/lib/phaseStepper';
import type { WsEvent } from '@/types/websocket';

describe('derivePhaseStepper', () => {
  it('returns null when no stage_status event has been seen', () => {
    expect(derivePhaseStepper([])).toBeNull();
    expect(derivePhaseStepper([
      { type: 'tool_start', tool: 'compute', step_num: 1 },
    ] as unknown as WsEvent[])).toBeNull();
  });

  it('activates 规划 on the planning stage', () => {
    const state = derivePhaseStepper([
      { type: 'stage_status', stage: 'planning', message: '制定计划' },
    ] as WsEvent[]);
    expect(state?.current).toBe('planning');
    expect(state?.phases.map((phase) => phase.status)).toEqual([
      'active', 'pending', 'pending', 'pending',
    ]);
  });

  it('advances to 命题审查 and ticks 规划', () => {
    const state = derivePhaseStepper([
      { type: 'stage_status', stage: 'planning' },
      { type: 'stage_status', stage: 'claim_check' },
    ] as WsEvent[]);
    expect(state?.current).toBe('claim_check');
    expect(state?.phases.map((phase) => phase.status)).toEqual([
      'done', 'active', 'pending', 'pending',
    ]);
  });

  it('treats a non-claim_check tool_start as 求解', () => {
    const state = derivePhaseStepper([
      { type: 'stage_status', stage: 'planning' },
      { type: 'tool_start', tool: 'compute', step_num: 1 },
    ] as unknown as WsEvent[]);
    expect(state?.current).toBe('solving');
    expect(state?.phases.map((phase) => phase.status)).toEqual([
      'done', 'done', 'active', 'pending',
    ]);
  });

  it('keeps claim_check tool calls inside 命题审查', () => {
    const state = derivePhaseStepper([
      { type: 'stage_status', stage: 'claim_check' },
      { type: 'tool_start', tool: 'compute', step_num: 'claim_check' },
    ] as unknown as WsEvent[]);
    expect(state?.current).toBe('claim_check');
  });

  it('treats model-related stages as 求解', () => {
    const state = derivePhaseStepper([
      { type: 'stage_status', stage: 'planning' },
      { type: 'stage_status', stage: 'thinking', message: 'Entering reasoning loop.' },
    ] as WsEvent[]);
    expect(state?.current).toBe('solving');
  });

  it('maps reviewer stages to 审查', () => {
    for (const stage of ['reviewer', 'reviewer_panel']) {
      const state = derivePhaseStepper([
        { type: 'stage_status', stage: 'planning' },
        { type: 'stage_status', stage: 'thinking' },
        { type: 'stage_status', stage },
      ] as WsEvent[]);
      expect(state?.current).toBe('review');
      expect(state?.phases.map((phase) => phase.status)).toEqual([
        'done', 'done', 'done', 'active',
      ]);
    }
  });

  it('never regresses to an earlier phase', () => {
    const state = derivePhaseStepper([
      { type: 'stage_status', stage: 'thinking' },
      { type: 'stage_status', stage: 'claim_check' },
    ] as WsEvent[]);
    expect(state?.current).toBe('solving');
  });

  it('marks every phase done after the done event', () => {
    const state = derivePhaseStepper([
      { type: 'stage_status', stage: 'planning' },
      { type: 'stage_status', stage: 'thinking' },
      { type: 'done', final_answer: '42' },
    ] as unknown as WsEvent[]);
    expect(state?.finished).toBe(true);
    expect(state?.current).toBeNull();
    expect(state?.phases.every((phase) => phase.status === 'done')).toBe(true);
  });
});
