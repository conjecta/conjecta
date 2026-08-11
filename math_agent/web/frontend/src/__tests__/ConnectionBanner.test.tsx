// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import { QUOTA_EXCEEDED_MESSAGE } from '@/lib/publicError';
import { useUiStore } from '@/store/ui';

afterEach(() => {
  cleanup();
  useUiStore.setState({
    usageDialogOpen: false,
    usageDialogReason: 'default',
  });
});

describe('ConnectionBanner', () => {
  it('renders a generic error banner', () => {
    render(<ConnectionBanner message="服务暂时遇到问题，请稍后重试。" onClose={() => {}} />);
    expect(screen.getByRole('alert')).toHaveTextContent('服务暂时遇到问题');
    expect(screen.queryByRole('button', { name: '去绑定 API Key' })).not.toBeInTheDocument();
  });

  it('highlights quota exhaustion and opens the usage dialog', async () => {
    render(<ConnectionBanner message={QUOTA_EXCEEDED_MESSAGE} onClose={() => {}} />);

    expect(screen.getByRole('alert')).toHaveTextContent('今日免费额度已用完');
    expect(screen.getByText(QUOTA_EXCEEDED_MESSAGE)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '去绑定 API Key' }));
    expect(useUiStore.getState().usageDialogOpen).toBe(true);
    expect(useUiStore.getState().usageDialogReason).toBe('quota_exceeded');
  });
});
