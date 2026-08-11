// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryDialog } from '@/components/MemoryDialog';

vi.mock('@/api/memories', () => ({
  fetchUserMemories: vi.fn(),
  updateUserMemory: vi.fn(),
  deleteUserMemory: vi.fn(),
  clearUserProfile: vi.fn(),
}));

import * as memoriesApi from '@/api/memories';

const fetchUserMemories = vi.mocked(memoriesApi.fetchUserMemories);
const updateUserMemory = vi.mocked(memoriesApi.updateUserMemory);
const deleteUserMemory = vi.mocked(memoriesApi.deleteUserMemory);
const clearUserProfile = vi.mocked(memoriesApi.clearUserProfile);

const activeMemory = {
  id: 'um-1',
  kind: 'preference' as const,
  content: '用中文回答',
  why: '用户明确提出',
  weight: 0.92,
  status: 'active' as const,
  scope: 'global',
  created_at: '2026-07-14T10:00:00Z',
  updated_at: '2026-07-14T10:00:00Z',
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('MemoryDialog', () => {
  it('shows the profile, memory scope, and status counts', async () => {
    fetchUserMemories.mockResolvedValue({
      memories: [activeMemory],
      profile: {
        summary: '偏好简洁的中文解释',
        version: 3,
        generated_at: '2026-07-14T10:00:00Z',
      },
    });

    render(<MemoryDialog onClose={() => {}} />);

    expect(await screen.findByText('用中文回答')).toBeInTheDocument();
    expect(screen.getByText('偏好简洁的中文解释')).toBeInTheDocument();
    expect(screen.getByText('所有项目')).toBeInTheDocument();
    expect(screen.getByText('1 使用中')).toBeInTheDocument();
  });

  it('pauses an active memory', async () => {
    fetchUserMemories.mockResolvedValue({ memories: [activeMemory], profile: null });
    updateUserMemory.mockResolvedValue({
      ...activeMemory,
      status: 'snoozed',
      updated_at: '2026-07-14T11:00:00Z',
    });

    render(<MemoryDialog onClose={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '暂停' }));

    await waitFor(() =>
      expect(updateUserMemory).toHaveBeenCalledWith('um-1', { status: 'snoozed' }),
    );
    expect(await screen.findByText('已暂停')).toBeInTheDocument();
  });

  it('requires confirmation before clearing the profile', async () => {
    fetchUserMemories.mockResolvedValue({
      memories: [],
      profile: {
        summary: '偏好简洁的中文解释',
        version: 3,
        generated_at: '2026-07-14T10:00:00Z',
      },
    });
    clearUserProfile.mockResolvedValue(undefined);

    render(<MemoryDialog onClose={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '清除整体理解' }));

    expect(clearUserProfile).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: '确认清除' }));

    await waitFor(() => expect(clearUserProfile).toHaveBeenCalled());
    expect(screen.queryByText('偏好简洁的中文解释')).not.toBeInTheDocument();
  });

  it('requires confirmation before deleting a memory', async () => {
    fetchUserMemories.mockResolvedValue({ memories: [activeMemory], profile: null });
    deleteUserMemory.mockResolvedValue(undefined);

    render(<MemoryDialog onClose={() => {}} />);
    await userEvent.click(
      await screen.findByRole('button', { name: '删除记忆：用中文回答' }),
    );

    expect(screen.getByText(/删除后不会自动重新添加/)).toBeInTheDocument();
    expect(deleteUserMemory).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: '确认删除' }));

    await waitFor(() => expect(deleteUserMemory).toHaveBeenCalledWith('um-1'));
    expect(screen.queryByText('用中文回答')).not.toBeInTheDocument();
  });

  it('explains an empty memory state', async () => {
    fetchUserMemories.mockResolvedValue({ memories: [], profile: null });

    render(<MemoryDialog onClose={() => {}} />);

    expect(await screen.findByText('还没有长期记忆')).toBeInTheDocument();
  });
});
