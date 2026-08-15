// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
    today: {
      prompt_tokens: 600,
      completion_tokens: 400,
      total_tokens: 1000,
      cost_usd: 0.05,
      quota_tokens: 500000,
      remaining_tokens: 499000,
    },
    this_month: {
      prompt_tokens: 3000,
      completion_tokens: 2000,
      total_tokens: 5000,
      cost_usd: 0.25,
    },
  });
}

function savedEndpoint(baseUrl = 'https://api.example.com/v1') {
  return {
    base_url: baseUrl,
    model: 'gpt-5.6-sol',
    requires_rebind: false,
    updated_at: '2026-07-14T10:00:00Z',
  };
}

describe('UsageDialog', () => {
  it('renders usage summary and the fixed user model', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue(null);

    render(<UsageDialog onClose={() => {}} />);

    expect(await screen.findByText(/今日 Token/)).toBeInTheDocument();
    expect(screen.getByText(/今日成本/)).toBeInTheDocument();
    expect(screen.getByText(/本月 Token/)).toBeInTheDocument();
    expect(screen.getByText(/本月成本/)).toBeInTheDocument();
    expect(screen.getByText('gpt-5.6-sol')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('saves a Base URL and API key', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue(null);
    setApiKey.mockResolvedValue(savedEndpoint());

    render(<UsageDialog onClose={() => {}} />);

    await userEvent.type(await screen.findByLabelText('Base URL'), 'https://api.example.com/v1');
    await userEvent.type(screen.getByLabelText('API Key'), 'sk-test');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(setApiKey).toHaveBeenCalledWith('https://api.example.com/v1', 'sk-test'),
    );
    expect(await screen.findByText(/已绑定 https:\/\/api\.example\.com\/v1/)).toBeInTheDocument();
  });

  it('loads an existing Base URL and trims inputs before saving', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue(savedEndpoint());
    setApiKey.mockResolvedValue(savedEndpoint());

    render(<UsageDialog onClose={() => {}} />);

    const baseUrlInput = await screen.findByLabelText('Base URL');
    expect(baseUrlInput).toHaveValue('https://api.example.com/v1');
    await userEvent.clear(baseUrlInput);
    await userEvent.type(baseUrlInput, '  https://api.example.com/v1  ');
    await userEvent.type(screen.getByLabelText('API Key'), '  sk-padded  ');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(setApiKey).toHaveBeenCalledWith('https://api.example.com/v1', 'sk-padded'),
    );
  });

  it('rejects a non-HTTPS Base URL before calling the API', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue(null);

    render(<UsageDialog onClose={() => {}} />);

    await userEvent.type(await screen.findByLabelText('Base URL'), 'http://api.example.com/v1');
    await userEvent.type(screen.getByLabelText('API Key'), 'sk-test');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(await screen.findByText('Base URL 必须使用 HTTPS')).toBeInTheDocument();
    expect(setApiKey).not.toHaveBeenCalled();
  });

  it('shows that a legacy provider record must be rebound', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue({
      base_url: null,
      model: 'gpt-5.6-sol',
      requires_rebind: true,
      updated_at: '2026-07-14T10:00:00Z',
    });

    render(<UsageDialog onClose={() => {}} />);

    expect(await screen.findByText(/旧版平台配置需要重新填写/)).toBeInTheDocument();
  });

  it('deletes an API key', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue(savedEndpoint());
    deleteApiKey.mockResolvedValue(undefined);

    render(<UsageDialog onClose={() => {}} />);

    await userEvent.click(await screen.findByRole('button', { name: /删除已绑定 key/i }));

    await waitFor(() => expect(deleteApiKey).toHaveBeenCalled());
    expect(screen.queryByText(/已绑定 https:/)).not.toBeInTheDocument();
  });

  it('displays an error when usage fails to load', async () => {
    fetchUsage.mockRejectedValue(new Error('Network error'));
    fetchApiKey.mockResolvedValue(null);

    render(<UsageDialog onClose={() => {}} />);

    expect(await screen.findByText(/Network error/)).toBeInTheDocument();
  });

  it('displays an error when saving an API key fails', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue(null);
    setApiKey.mockRejectedValue(new Error('Save failed'));

    render(<UsageDialog onClose={() => {}} />);

    await userEvent.type(await screen.findByLabelText('Base URL'), 'https://api.example.com/v1');
    await userEvent.type(screen.getByLabelText('API Key'), 'sk-test');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(await screen.findByText(/Save failed/)).toBeInTheDocument();
  });

  it('does not render NaN progress width when quota_tokens is zero', async () => {
    fetchUsage.mockResolvedValue({
      today: {
        prompt_tokens: 600,
        completion_tokens: 400,
        total_tokens: 1000,
        cost_usd: 0.05,
        quota_tokens: 0,
        remaining_tokens: 0,
      },
      this_month: {
        prompt_tokens: 3000,
        completion_tokens: 2000,
        total_tokens: 5000,
        cost_usd: 0.25,
      },
    });
    fetchApiKey.mockResolvedValue(null);

    render(<UsageDialog onClose={() => {}} />);

    await screen.findByText(/今日 Token/);
    const progress = screen.getByRole('progressbar');

    expect(progress).toHaveStyle({ width: '0%' });
  });

  it('shows a quota-exceeded callout when opened for that reason', async () => {
    mockDefaultUsage();
    fetchApiKey.mockResolvedValue(null);

    render(<UsageDialog onClose={() => {}} reason="quota_exceeded" />);

    expect(await screen.findByText('今日免费额度已用完')).toBeInTheDocument();
    expect(screen.getByText(/绑定 OpenAI 兼容 Base URL 和 API Key/)).toBeInTheDocument();
  });
});
