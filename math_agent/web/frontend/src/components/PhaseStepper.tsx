import { Check } from 'lucide-react';
import type { PhaseStepperState } from '@/lib/phaseStepper';

/** Horizontal 规划 → 命题审查 → 求解 → 审查 stepper.
 * Active phase pulses, finished phases get a check, upcoming stay gray. */
export function PhaseStepper({ state }: { state: PhaseStepperState }) {
  return (
    <ol className="flex items-center gap-1.5" aria-label="求解阶段">
      {state.phases.map((phase, index) => (
        <li key={phase.id} className="flex items-center gap-1.5">
          {index > 0 ? <span className="h-px w-3 bg-border" aria-hidden="true" /> : null}
          <span
            className={`flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
              phase.status === 'active'
                ? 'bg-primary/10 text-primary'
                : phase.status === 'done'
                  ? 'text-success'
                  : 'text-muted-foreground/60'
            }`}
            aria-current={phase.status === 'active' ? 'step' : undefined}
          >
            {phase.status === 'done' ? (
              <Check size={11} className="shrink-0" aria-hidden="true" />
            ) : (
              <span
                aria-hidden="true"
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                  phase.status === 'active'
                    ? 'bg-primary motion-safe:animate-pulse'
                    : 'bg-muted-foreground/40'
                }`}
              />
            )}
            {phase.label}
          </span>
        </li>
      ))}
    </ol>
  );
}
