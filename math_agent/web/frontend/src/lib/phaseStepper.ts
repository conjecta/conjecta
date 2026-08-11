import type { WsEvent } from '@/types/websocket';

export type PhaseId = 'planning' | 'claim_check' | 'solving' | 'review';
export type PhaseStatus = 'pending' | 'active' | 'done';

export interface PhaseState {
  id: PhaseId;
  label: string;
  status: PhaseStatus;
}

export interface PhaseStepperState {
  phases: PhaseState[];
  /** Currently active phase, or null once the run finished. */
  current: PhaseId | null;
  finished: boolean;
}

const PHASE_ORDER: PhaseId[] = ['planning', 'claim_check', 'solving', 'review'];

const PHASE_LABELS: Record<PhaseId, string> = {
  planning: '规划',
  claim_check: '命题审查',
  solving: '求解',
  review: '审查',
};

/** Map a stage_status `stage` value onto a stepper phase.
 * planning → 规划, claim_check → 命题审查, reviewer / reviewer_panel /
 * reviewing → 审查; any other (model-related) stage counts as 求解. */
function stageToPhaseIndex(stage: string): number {
  const key = stage.toLowerCase().replace(/\s+/g, '_');
  if (key === 'planning') return 0;
  if (key === 'claim_check') return 1;
  if (key === 'reviewer' || key === 'reviewer_panel' || key === 'reviewing') return 3;
  return 2;
}

/** Derive the 规划 → 命题审查 → 求解 → 审查 stepper state from the event stream.
 *
 * Rules:
 * - Returns null when no stage_status event has been seen (stepper hidden).
 * - The furthest phase ever reached is active; earlier phases are done.
 * - A tool_start with step_num "claim_check" belongs to 命题审查; any other
 *   tool_start implies 求解 (even without an explicit stage event).
 * - A `done` event marks every phase done. */
export function derivePhaseStepper(events: WsEvent[]): PhaseStepperState | null {
  let sawStageStatus = false;
  let maxIndex = -1;
  let finished = false;

  for (const event of events) {
    const data = event as Record<string, unknown>;
    if (event.type === 'stage_status') {
      sawStageStatus = true;
      const stage = typeof data.stage === 'string' ? data.stage : '';
      if (!stage) continue;
      const index = stageToPhaseIndex(stage);
      if (index > maxIndex) maxIndex = index;
    } else if (event.type === 'tool_start') {
      const index = data.step_num === 'claim_check' ? 1 : 2;
      if (index > maxIndex) maxIndex = index;
    } else if (event.type === 'done') {
      finished = true;
    }
  }

  if (!sawStageStatus) return null;

  const phases: PhaseState[] = PHASE_ORDER.map((id, index) => ({
    id,
    label: PHASE_LABELS[id],
    status: index < maxIndex || finished ? 'done' : index === maxIndex ? 'active' : 'pending',
  }));

  return {
    phases,
    current: finished || maxIndex < 0 ? null : PHASE_ORDER[maxIndex],
    finished,
  };
}
