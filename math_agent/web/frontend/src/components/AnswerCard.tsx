import type { ReactNode } from 'react';
import { CopyButton } from './CopyButton';
import { MathText } from './MathText';

interface AnswerCardProps {
  text: string;
  /** Optional badge; defaults to 最终答案. */
  title?: string;
  titleClassName?: string;
  badges?: ReactNode;
  footer?: ReactNode;
  copyLabel?: string;
  className?: string;
}

/** Shared final-answer surface used by live TraceItem and conversation history. */
export function AnswerCard({
  text,
  title = '最终答案',
  titleClassName = 'text-success',
  badges,
  footer,
  copyLabel = '复制答案',
  className = '',
}: AnswerCardProps) {
  if (!text) return null;
  return (
    <div
      className={`group my-3 rounded-xl border border-success/25 bg-card px-4 py-4 text-sm text-foreground shadow-sm ${className}`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className={`text-xs font-semibold ${titleClassName}`}>{title}</div>
          {badges}
        </div>
        <CopyButton text={text} label={copyLabel} />
      </div>
      <MathText text={text} />
      {footer}
    </div>
  );
}
