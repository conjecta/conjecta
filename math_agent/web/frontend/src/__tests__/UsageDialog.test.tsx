// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, it, expect, vi } from 'vitest';
import { UsageDialog } from '@/components/UsageDialog';

vi.mock('@/api/billing', () => ({
  fetchUsage: vi.fn(),
  fetchApiKey: vi.fn(),
  setApiKey: vi.fn(),
  deleteApiKey: vi.fn(),
}));

import * as billing from '@/api/billing';

const fetchUsage = vi.mocked(billing.fetchUsage);
const fetchApiKey = vi.mocked(billing.fetchApiKey);
const setApiKey = vi.mocked(billing.setApiKey);
const deleteApiKey = vi.mocked(billing.deleteApiKey);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function mockDefaultUsage() {
  fetchUsage.mockResolvedValue({
    today: { prompt_tokens: 600, completion_tokens: 400, total_tokens: 1000, cost_usd: 0.05, quota_tokens: 500000, remaining_tokens: 499000 },
    this_month: { prompt_tokens: 3000, completion_tokens: 2000, total_tokens: 5000, cost_usd: 0.25 },
  });
}

describe('UsageDialog', () => {
  it('renders usage summary', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue(null);

    render(<UsageDialog onClose={() => {}} />);

    expect(await screen.findByText(/今日 Token/)).toBeInTheDocument();
    expect(screen.getByText(/今日成本/)).toBeInTheDocument();
    expect(screen.getByText(/本月 Token/)).toBeInTheDocument();
    expect(screen.getByText(/本月成本/)).toBeInTheDocument();
  });

  it('uses the fixed OpenAI provider when saving', async () => {
    fetchUsage.mockResolvedValue({
      today: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0, quota_tokens: 1, remaining_tokens: 1 },
      this_month: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0 },
    });
    fetchApiKey.mockResolvedValue(null);
    setApiKey.mockResolvedValue({ provider: 'openai', updated_at: '2026-07-14T10:00:00Z' });

    render(<UsageDialog onClose={() => {}} />);

    const trigger = await screen.findByRole('combobox');
    expect(trigger).toHaveTextContent('OpenAI');

    await userEvent.type(screen.getByPlaceholderText('输入 API key'), 'sk-openai');
    await userEvent.click(screen.getByRole('button', { name: /保存/i }));

    await waitFor(() => expect(setApiKey).toHaveBeenCalledWith('openai', 'sk-openai'));
  });

  it('saves an API key', async () => {
    fetchUsage.mockResolvedValue({
      today: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0, quota_tokens: 1, remaining_tokens: 1 },
      this_month: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0 },
    });
    fetchApiKey.mockResolvedValue(null);
    setApiKey.mockResolvedValue({ provider: 'openai', updated_at: '2026-07-14T10:00:00Z' });

    render(<UsageDialog onClose={() => {}} />);

    await screen.findByRole('combobox');
    await userEvent.type(screen.getByPlaceholderText('输入 API key'), 'sk-test');
    await userEvent.click(screen.getByRole('button', { name: /保存/i }));

    await waitFor(() => expect(setApiKey).toHaveBeenCalledWith('openai', 'sk-test'));
    expect(await screen.findByText(/已绑定 openai/)).toBeInTheDocument();
  });

  it('trims the API key before saving', async () => {
    fetchUsage.mockResolvedValue({
      today: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0, quota_tokens: 1, remaining_tokens: 1 },
      this_month: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0 },
    });
    fetchApiKey.mockResolvedValue(null);
    setApiKey.mockResolvedValue({ provider: 'openai', updated_at: '2026-07-14T10:00:00Z' });

    render(<UsageDialog onClose={() => {}} />);

    await screen.findByRole('combobox');
    await userEvent.type(screen.getByPlaceholderText('输入 API key'), '  sk-padded  ');
    await userEvent.click(screen.getByRole('button', { name: /保存/i }));

    await waitFor(() => expect(setApiKey).toHaveBeenCalledWith('openai', 'sk-padded'));
  });

  it('deletes an API key', async () => {
    fetchUsage.mockResolvedValue({
      today: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0, quota_tokens: 1, remaining_tokens: 1 },
      this_month: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0 },
    });
    fetchApiKey.mockResolvedValue({ provider: 'openai', updated_at: '2026-07-14T10:00:00Z' });
    deleteApiKey.mockResolvedValue(undefined);

    render(<UsageDialog onClose={() => {}} />);

    const deleteButton = await screen.findByRole('button', { name: /删除已绑定 key/i });
    await userEvent.click(deleteButton);

    await waitFor(() => expect(deleteApiKey).toHaveBeenCalled());
    expect(screen.queryByText(/已绑定/)).not.toBeInTheDocument();
  });

  it('displays an error when usage fails to load', async () => {
    fetchUsage.mockRejectedValue(new Error('Network error'));
    fetchApiKey.mockResolvedValue(null);

    render(<UsageDialog onClose={() => {}} />);

    expect(await screen.findByText(/Network error/)).toBeInTheDocument();
  });

  it('displays an error when saving an API key fails', async () => {
    fetchUsage.mockResolvedValue({
      today: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0, quota_tokens: 1, remaining_tokens: 1 },
      this_month: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0 },
    });
    fetchApiKey.mockResolvedValue(null);
    setApiKey.mockRejectedValue(new Error('Save failed'));

    render(<UsageDialog onClose={() => {}} />);

    await screen.findByRole('combobox');
    await userEvent.type(screen.getByPlaceholderText('输入 API key'), 'sk-test');
    await userEvent.click(screen.getByRole('button', { name: /保存/i }));

    expect(await screen.findByText(/Save failed/)).toBeInTheDocument();
  });

  it('does not render NaN progress width when quota_tokens is zero', async () => {
    fetchUsage.mockResolvedValue({
      today: { prompt_tokens: 600, completion_tokens: 400, total_tokens: 1000, cost_usd: 0.05, quota_tokens: 0, remaining_tokens: 0 },
      this_month: { prompt_tokens: 3000, completion_tokens: 2000, total_tokens: 5000, cost_usd: 0.25 },
    });
    fetchApiKey.mockResolvedValue(null);

    render(<UsageDialog onClose={() => {}} />);

    await screen.findByText(/今日 Token/);
    const progress = document.querySelector('[role="progressbar"]') as HTMLElement;

    expect(progress.style.width).not.toContain('NaN');
    expect(progress.style.width).toBe('0%');
  });

  it('shows a quota-exceeded callout when opened for that reason', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue(null);

    render(<UsageDialog onClose={() => {}} reason="quota_exceeded" />);

    expect(await screen.findByText('今日免费额度已用完')).toBeInTheDocument();
    expect(
      screen.getByText(/绑定 OpenAI API Key/),
    ).toBeInTheDocument();
  });
});
