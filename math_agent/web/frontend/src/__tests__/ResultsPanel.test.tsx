// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { ResultsPanel } from '../components/ResultsPanel';
import * as queries from '../api/queries';
import { useUiStore } from '../store/ui';

vi.mock('../api/queries', () => ({
  useProject: vi.fn(),
}));

const turns = [
  {
    id: 't1',
    conversation_id: 'conv-1',
    problem: '证明根号2是无理数',
    answer: '假设 $\\sqrt{2}=p/q$，则推出矛盾，所以根号2是无理数。',
    verification_status: 'verified',
    strategy: 'research',
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 't2',
    conversation_id: 'conv-1',
    problem: '求 x 趋近于 0 时 sin(x)/x 的极限',
    answer: '由夹逼定理可得极限为 1。',
    verification_status: 'blocked',
    created_at: '2026-01-02T00:00:00Z',
  },
];

function mockProject(data: unknown) {
  vi.mocked(queries.useProject).mockReturnValue({
    data,
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof queries.useProject>);
}

describe('ResultsPanel', () => {
  afterEach(() => {
    cleanup();
    useUiStore.setState({ resultDrawerTurnId: null, selectedConversationId: null });
  });

  beforeEach(() => {
    useUiStore.setState({
      selectedProjectId: 'default',
      selectedOwnerUserId: null,
      selectedConversationId: 'conv-1',
      resultDrawerTurnId: null,
    });
    mockProject({ id: 'default', name: 'default', turns });
  });

  it('renders turn cards newest-first with chronological numbering and badges', () => {
    render(<ResultsPanel />);
    expect(screen.getByText('轮 2')).toBeInTheDocument();
    expect(screen.getByText('轮 1')).toBeInTheDocument();
    expect(screen.getByText('已验证')).toBeInTheDocument();
    expect(screen.getByText('受阻')).toBeInTheDocument();
    expect(screen.getByText('研究')).toBeInTheDocument();
    expect(screen.getByText(/求 x 趋近于 0 时/)).toBeInTheDocument();
    // Answer summary strips LaTeX delimiters but keeps the body text.
    expect(screen.getByText(/假设 \\sqrt\{2\}=p\/q/)).toBeInTheDocument();
    // Newest turn renders above the older one.
    const newest = screen.getByText('轮 2');
    const oldest = screen.getByText('轮 1');
    expect(newest.compareDocumentPosition(oldest) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows the empty state when the conversation has no turns', () => {
    useUiStore.setState({ selectedConversationId: 'conv-missing' });
    render(<ResultsPanel />);
    expect(screen.getByText('完成一轮求解后，结论会沉淀在这里')).toBeInTheDocument();
  });

  it('opens the result drawer when a card is clicked', async () => {
    render(<ResultsPanel />);
    await userEvent.click(screen.getByText(/求 x 趋近于 0 时/));
    expect(useUiStore.getState().resultDrawerTurnId).toBe('t2');
  });
});
