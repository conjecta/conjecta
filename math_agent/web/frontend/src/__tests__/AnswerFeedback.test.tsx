// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AnswerFeedback } from '@/components/AnswerFeedback';

vi.mock('@/api/feedback', () => ({
  submitFeedback: vi.fn().mockResolvedValue({ ok: true }),
}));

import { submitFeedback } from '@/api/feedback';

describe('AnswerFeedback', () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('submits satisfied rating inline', async () => {
    render(
      <AnswerFeedback
        outcome="completed"
        sessionId="sess-1"
        problemPreview="prove 1+1=2"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '满意' }));
    fireEvent.click(screen.getByRole('button', { name: '提交反馈' }));
    await waitFor(() => {
      expect(submitFeedback).toHaveBeenCalledWith(
        expect.objectContaining({
          rating: 'satisfied',
          outcome: 'completed',
          session_id: 'sess-1',
        }),
      );
      expect(screen.getByText(/感谢反馈/)).toBeInTheDocument();
    });
  });

  it('dismiss hides the prompt without API call', async () => {
    render(
      <AnswerFeedback
        outcome="failed"
        sessionId={null}
        problemPreview=""
      />,
    );
    expect(screen.getByText(/这次回答有帮助吗/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '收起' }));
    await waitFor(() => {
      expect(screen.queryByText(/这次回答有帮助吗/)).not.toBeInTheDocument();
      expect(screen.queryByText(/感谢反馈/)).not.toBeInTheDocument();
    });
    expect(submitFeedback).not.toHaveBeenCalled();
  });
});
