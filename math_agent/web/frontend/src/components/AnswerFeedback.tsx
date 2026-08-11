import { useEffect, useState } from 'react';
import { submitFeedback } from '@/api/feedback';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import type { FeedbackOutcome, FeedbackRating } from '@/types/feedback';

interface AnswerFeedbackProps {
  outcome: FeedbackOutcome;
  sessionId: string | null;
  problemPreview: string;
}

type FeedbackPhase = 'prompt' | 'submitted' | 'dismissed';

export function AnswerFeedback({
  outcome,
  sessionId,
  problemPreview,
}: AnswerFeedbackProps) {
  const [rating, setRating] = useState<FeedbackRating | null>(null);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<FeedbackPhase>('prompt');

  useEffect(() => {
    setRating(null);
    setComment('');
    setSubmitting(false);
    setError(null);
    setPhase('prompt');
  }, [sessionId, outcome]);

  const handleSubmit = async () => {
    if (!rating || submitting || phase !== 'prompt') return;
    setSubmitting(true);
    setError(null);
    try {
      await submitFeedback({
        rating,
        outcome,
        comment: comment.trim() || undefined,
        session_id: sessionId,
        problem_preview: problemPreview || undefined,
      });
      setPhase('submitted');
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交失败，请稍后重试');
      setSubmitting(false);
    }
  };

  if (phase === 'dismissed') return null;

  if (phase === 'submitted') {
    return (
      <div
        data-testid="answer-feedback"
        className="my-4 rounded-lg border border-border/70 bg-muted/25 px-3 py-2.5 text-xs text-muted-foreground"
      >
        感谢反馈，我们会据此改进解题质量。
      </div>
    );
  }

  return (
    <div
      data-testid="answer-feedback"
      className="my-4 rounded-lg border border-border/70 bg-muted/25 px-3 py-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-foreground/80">这次回答有帮助吗？</span>
        <Button
          type="button"
          size="sm"
          variant={rating === 'satisfied' ? 'default' : 'outline'}
          aria-pressed={rating === 'satisfied'}
          onClick={() => setRating('satisfied')}
          disabled={submitting}
        >
          满意
        </Button>
        <Button
          type="button"
          size="sm"
          variant={rating === 'unsatisfied' ? 'default' : 'outline'}
          aria-pressed={rating === 'unsatisfied'}
          onClick={() => setRating('unsatisfied')}
          disabled={submitting}
        >
          不满意
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="ml-auto text-muted-foreground"
          onClick={() => setPhase('dismissed')}
          disabled={submitting}
        >
          收起
        </Button>
      </div>

      {rating ? (
        <div className="mt-3 space-y-2">
          <Textarea
            aria-label="补充意见"
            placeholder="可选：告诉我们哪里做得好或需要改进"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            disabled={submitting}
            className="min-h-[72px] text-sm"
          />
          {error && (
            <div
              role="alert"
              className="rounded border border-destructive bg-destructive/10 px-3 py-2 text-xs text-destructive"
            >
              {error}
            </div>
          )}
          <div className="flex justify-end">
            <Button
              type="button"
              size="sm"
              onClick={() => {
                void handleSubmit();
              }}
              disabled={submitting}
            >
              {submitting ? '提交中…' : '提交反馈'}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
