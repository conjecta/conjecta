// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { ExplorerPanel } from '../components/ExplorerPanel';
import * as queries from '../api/queries';
import { useUiStore } from '../store/ui';

vi.mock('../api/queries', () => ({
  useProject: vi.fn(),
  useProjects: vi.fn(),
  useDeleteConversation: vi.fn(),
  useRenameProject: vi.fn(),
}));

vi.mock('@/api/friends', () => ({
  listProjectMembers: vi.fn(async () => ({ ok: true, members: [] })),
  listFriends: vi.fn(async () => ({ ok: true, friends: [] })),
  addProjectMember: vi.fn(),
  removeProjectMember: vi.fn(),
}));

describe('ExplorerPanel', () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    useUiStore.setState({
      selectedProjectId: 'alpha',
      selectedOwnerUserId: null,
      selectedConversationId: null,
      chatResetKey: 0,
    });
    vi.mocked(queries.useProjects).mockReturnValue({
      data: { projects: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useProjects>);
    vi.mocked(queries.useRenameProject).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof queries.useRenameProject>);
    vi.mocked(queries.useProject).mockReturnValue({
      data: {
        id: 'alpha',
        turns: [
          { id: 't1', conversation_id: 'c1', problem: 'Prove x=x', answer: 'By reflexivity.' },
          { id: 't2', conversation_id: 'c1', problem: 'Can you explain?', answer: 'Another answer.' },
          { id: 't3', conversation_id: 'c2', problem: 'Another question', answer: 'Separate answer.' },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useProject>);
    vi.mocked(queries.useDeleteConversation).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof queries.useDeleteConversation>);
  });

  it('groups multiple turns into one conversation', () => {
    render(<ExplorerPanel />);
    expect(screen.getByRole('button', { name: '新对话' })).toBeInTheDocument();
    expect(screen.getByText('对话')).toBeInTheDocument();
    expect(screen.getByText('Prove x=x')).toBeInTheDocument();
    // Compact status line: 有结论 / 求解中 / 空, then the turn count.
    expect(screen.getAllByText(/有结论/).length).toBeGreaterThan(0);
    expect(screen.getByText(/2 轮/)).toBeInTheDocument();
    expect(screen.queryByText('Can you explain?')).not.toBeInTheDocument();
  });

  it('filters history by search text', async () => {
    render(<ExplorerPanel />);
    await userEvent.type(screen.getByLabelText(/搜索对话/i), 'separate');
    expect(screen.queryByText('Prove x=x')).not.toBeInTheDocument();
    expect(screen.getByText('Another question')).toBeInTheDocument();
  });

  it('starts a new problem from sidebar control', async () => {
    render(<ExplorerPanel />);
    await userEvent.click(screen.getByRole('button', { name: '新对话' }));
    expect(useUiStore.getState().selectedConversationId).toBeNull();
    expect(useUiStore.getState().chatResetKey).toBe(1);
  });

  it('selects a conversation on click', async () => {
    render(<ExplorerPanel />);
    await userEvent.click(screen.getByText('Prove x=x'));
    expect(useUiStore.getState().selectedConversationId).toBe('c1');
  });

  it('confirms before deleting a conversation', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ ok: true, deleted: 2 });
    vi.mocked(queries.useDeleteConversation).mockReturnValue({
      mutateAsync,
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof queries.useDeleteConversation>);
    useUiStore.setState({ selectedConversationId: 'c1' });

    render(<ExplorerPanel />);
    const proveItem = screen.getByText('Prove x=x').closest('li');
    expect(proveItem).toBeTruthy();
    await userEvent.click(
      within(proveItem as HTMLElement).getByRole('button', { name: '删除对话' }),
    );
    expect(screen.getByText('确认删除？')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '确认删除对话' }));
    expect(mutateAsync).toHaveBeenCalledWith('c1');
    expect(useUiStore.getState().selectedConversationId).toBeNull();
  });

  it('does not expose the internal default project in the history UI', () => {
    useUiStore.setState({ selectedProjectId: 'default' });
    render(<ExplorerPanel />);
    expect(screen.queryByText(/项目：/i)).not.toBeInTheDocument();
    expect(screen.queryByText('default')).not.toBeInTheDocument();
  });

  it('renames the selected project from the sidebar', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ ok: true });
    vi.mocked(queries.useProjects).mockReturnValue({
      data: {
        projects: [
          { id: 'alpha', name: 'Alpha', role: 'lead', owner_user_id: 'u1' },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useProjects>);
    vi.mocked(queries.useRenameProject).mockReturnValue({
      mutateAsync,
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof queries.useRenameProject>);
    useUiStore.setState({ selectedProjectId: 'alpha', selectedOwnerUserId: 'u1' });

    render(<ExplorerPanel />);
    await userEvent.click(screen.getByRole('button', { name: '重命名项目' }));
    const input = screen.getByLabelText('项目名称');
    await userEvent.clear(input);
    await userEvent.type(input, 'Number Theory');
    await userEvent.click(screen.getByRole('button', { name: '保存项目名称' }));

    expect(mutateAsync).toHaveBeenCalledWith({
      projectId: 'alpha',
      name: 'Number Theory',
      ownerUserId: 'u1',
    });
  });

  it('hides rename for collaborator projects', () => {
    vi.mocked(queries.useProjects).mockReturnValue({
      data: {
        projects: [
          { id: 'alpha', name: 'Shared', role: 'collaborator', owner_user_id: 'owner' },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useProjects>);
    useUiStore.setState({ selectedProjectId: 'alpha', selectedOwnerUserId: 'owner' });

    render(<ExplorerPanel />);
    expect(screen.queryByRole('button', { name: '重命名项目' })).not.toBeInTheDocument();
  });

  it('keeps turn results collapsed behind a disclosure above the history list', async () => {
    useUiStore.setState({ selectedConversationId: 'c1' });
    render(<ExplorerPanel />);

    const toggle = screen.getByRole('button', { name: /结论/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('2 轮')).toBeInTheDocument();
    expect(screen.queryByText(/Can you explain\?/)).not.toBeInTheDocument();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(await screen.findByText(/Can you explain\?/)).toBeInTheDocument();
  });

  it('opens the project members panel from the header button and closes it three ways', async () => {
    render(<ExplorerPanel />);
    await userEvent.click(screen.getByRole('button', { name: '项目成员' }));
    expect(await screen.findByRole('dialog', { name: '项目协作成员' })).toBeInTheDocument();

    // 1. The styled X button closes the drawer.
    await userEvent.click(screen.getByRole('button', { name: '关闭' }));
    expect(screen.queryByRole('dialog', { name: '项目协作成员' })).not.toBeInTheDocument();

    // 2. Clicking the dimmed backdrop closes it.
    await userEvent.click(screen.getByRole('button', { name: '项目成员' }));
    const dialog = await screen.findByRole('dialog', { name: '项目协作成员' });
    await userEvent.click(dialog.parentElement!);
    expect(screen.queryByRole('dialog', { name: '项目协作成员' })).not.toBeInTheDocument();

    // 3. Escape closes it; clicks inside the drawer do not.
    await userEvent.click(screen.getByRole('button', { name: '项目成员' }));
    const reopened = await screen.findByRole('dialog', { name: '项目协作成员' });
    await userEvent.click(reopened);
    expect(screen.getByRole('dialog', { name: '项目协作成员' })).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: '项目协作成员' })).not.toBeInTheDocument();
  });
});
