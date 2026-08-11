// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { MainColumn } from '../components/MainColumn';
import { useUiStore } from '../store/ui';
import * as queries from '../api/queries';
import * as solveSocket from '../hooks/useSolveSocket';
import type { Status } from '../hooks/useSolveSocket';
import type { WsEvent } from '../types/websocket';

vi.mock('../api/queries', () => ({
  useProject: vi.fn(),
  queryKeys: { project: (id: string) => ['project', id] },
}));

vi.mock('../hooks/useSolveSocket', () => ({
  useSolveSocket: vi.fn(),
  finalAnswerFromEvents: vi.fn((events: WsEvent[]) => {
    const done = [...events].reverse().find((e) => e.type === 'done') as
      | { final_answer?: string; summary?: string }
      | undefined;
    if (!done) return '';
    if (typeof done.final_answer === 'string' && done.final_answer) return done.final_answer;
    return typeof done.summary === 'string' ? done.summary : '';
  }),
}));

vi.mock('../components/Composer', () => ({
  Composer: ({ sendProblem }: { sendProblem: (req: { problem: string; files?: unknown[] }) => void }) => (
    <div data-testid="composer">
      <button type="button" data-testid="send-q1" onClick={() => sendProblem({ problem: 'What is 1+1?' })}>
        q1
      </button>
      <button type="button" data-testid="send-q2" onClick={() => sendProblem({ problem: 'And 2+2?' })}>
        q2
      </button>
      <button
        type="button"
        data-testid="send-image"
        onClick={() => sendProblem({ problem: '请根据附件中的题目进行求解。', files: [{}] })}
      >
        image
      </button>
    </div>
  ),
}));

vi.mock('../components/ChatStage', () => ({
  ChatStage: ({
    feedback,
    composerSlot,
  }: {
    feedback?: {
      outcome: 'completed' | 'failed';
      sessionId: string | null;
      problemPreview: string;
    } | null;
    composerSlot?: ReactNode;
  }) => (
    <div data-testid="chat-stage">
      live stage
      {composerSlot}
      {feedback ? (
        <div data-testid="answer-feedback">这次回答有帮助吗？</div>
      ) : null}
    </div>
  ),
}));

function renderMain() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <MainColumn />
    </QueryClientProvider>,
  );
}

describe('MainColumn history view', () => {
  afterEach(() => cleanup());

  const clear = vi.fn();

  beforeEach(() => {
    clear.mockClear();
    useUiStore.setState({
      selectedProjectId: 'default',
      selectedConversationId: null,
      chatResetKey: 0,
    });
    vi.mocked(solveSocket.useSolveSocket).mockReturnValue({
      events: [],
      connectionError: null,
      clear,
      sendProblem: vi.fn(),
      interrupt: vi.fn(),
      status: 'idle',
    } as unknown as ReturnType<typeof solveSocket.useSolveSocket>);
    vi.mocked(queries.useProject).mockReturnValue({
      data: {
        turns: [
          {
            id: 'turn-1',
            conversation_id: 'conversation-1',
            problem: 'Prove u >= 0',
            answer: 'By the weak maximum principle, u(x) >= 0.',
          },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useProject>);
  });

  it('fills the main column with a single scrollable session history', () => {
    useUiStore.setState({ selectedConversationId: 'conversation-1' });
    renderMain();

    expect(screen.getByText(/By the weak maximum principle/i)).toBeInTheDocument();
    expect(screen.getByTestId('conversation-scroll')).toHaveClass('overflow-auto');
    expect(screen.getByTestId('conversation-history')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-stage')).not.toBeInTheDocument();
    expect(screen.getByTestId('composer')).toBeInTheDocument();
  });

  it('keeps prior turns and the live stage in the same scroll container', () => {
    useUiStore.setState({ selectedConversationId: 'conversation-1' });
    const { rerender } = renderMain();

    expect(screen.getByTestId('conversation-scroll')).toContainElement(
      screen.getByTestId('conversation-history'),
    );
    expect(screen.queryByTestId('chat-stage')).not.toBeInTheDocument();

    vi.mocked(solveSocket.useSolveSocket).mockReturnValue({
      events: [],
      connectionError: null,
      clear,
      sendProblem: vi.fn(),
      interrupt: vi.fn(),
      status: 'streaming',
    } as unknown as ReturnType<typeof solveSocket.useSolveSocket>);
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MainColumn />
      </QueryClientProvider>,
    );

    const scroll = screen.getByTestId('conversation-scroll');
    expect(scroll).toContainElement(screen.getByTestId('conversation-history'));
    expect(scroll).toContainElement(screen.getByTestId('chat-stage'));
    expect(screen.getByTestId('conversation-history')).not.toHaveClass('max-h-[46%]');
  });

  it('copies the problem and answer independently from history turns', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    useUiStore.setState({ selectedConversationId: 'conversation-1' });
    renderMain();

    await user.click(screen.getByRole('button', { name: '复制问题' }));
    expect(writeText).toHaveBeenCalledWith('Prove u >= 0');

    await user.click(screen.getByRole('button', { name: '复制答案' }));
    expect(writeText).toHaveBeenCalledWith('By the weak maximum principle, u(x) >= 0.');
  });
});

describe('MainColumn multi-turn conversation', () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    useUiStore.setState({
      selectedProjectId: 'default',
      selectedConversationId: null,
      chatResetKey: 0,
    });
    vi.mocked(queries.useProject).mockReturnValue({
      data: { turns: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useProject>);
  });

  it('keeps the first answer visible after sending a follow-up', async () => {
    const user = userEvent.setup();
    const sendProblem = vi.fn();
    const clear = vi.fn();
    let status: Status = 'idle';
    let events: WsEvent[] = [];

    const syncSocket = () => {
      vi.mocked(solveSocket.useSolveSocket).mockReturnValue({
        events,
        connectionError: null,
        clear,
        sendProblem,
        interrupt: vi.fn(),
        status,
      } as unknown as ReturnType<typeof solveSocket.useSolveSocket>);
    };
    syncSocket();

    const { rerender } = renderMain();
    const rerenderMain = () => {
      syncSocket();
      rerender(
        <QueryClientProvider client={new QueryClient()}>
          <MainColumn />
        </QueryClientProvider>,
      );
    };

    await user.click(screen.getByTestId('send-q1'));
    expect(screen.getByText('What is 1+1?')).toBeInTheDocument();

    status = 'done';
    events = [{ type: 'done', final_answer: 'The answer is 2.' } as WsEvent];
    rerenderMain();

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('answer-feedback')).toBeInTheDocument();

    await user.click(screen.getByTestId('send-q2'));
    status = 'streaming';
    events = [];
    rerenderMain();

    expect(screen.getByText('What is 1+1?')).toBeInTheDocument();
    expect(screen.getByText('The answer is 2.')).toBeInTheDocument();
    expect(screen.getByText('And 2+2?')).toBeInTheDocument();
    expect(sendProblem).toHaveBeenLastCalledWith(
      expect.objectContaining({
        problem: 'And 2+2?',
        conversation_history: [
          { role: 'user', text: 'What is 1+1?' },
          { role: 'assistant', text: 'The answer is 2.' },
        ],
        conversation_id: expect.any(String),
      }),
    );
  });

  it('shows inline answer feedback after done without opening a dialog', async () => {
    let status: Status = 'idle';
    const sessionId = 'sess-dedup';

    const syncSocket = () => {
      vi.mocked(solveSocket.useSolveSocket).mockReturnValue({
        events: [],
        connectionError: null,
        clear: vi.fn(),
        sendProblem: vi.fn(),
        interrupt: vi.fn(),
        status,
        sessionId,
      } as unknown as ReturnType<typeof solveSocket.useSolveSocket>);
    };
    syncSocket();

    const { rerender } = renderMain();
    const rerenderMain = () => {
      syncSocket();
      rerender(
        <QueryClientProvider client={new QueryClient()}>
          <MainColumn />
        </QueryClientProvider>,
      );
    };

    status = 'done';
    rerenderMain();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('answer-feedback')).toBeInTheDocument();
    expect(screen.getByText(/这次回答有帮助吗/)).toBeInTheDocument();
  });

  it('replaces the attachment placeholder with the extracted image problem during solving', async () => {
    const user = userEvent.setup();
    let events: WsEvent[] = [];
    const clear = vi.fn();
    const sendProblem = vi.fn();
    const interrupt = vi.fn();

    const syncSocket = () => {
      vi.mocked(solveSocket.useSolveSocket).mockReturnValue({
        events,
        connectionError: null,
        clear,
        sendProblem,
        interrupt,
        status: 'streaming',
      } as unknown as ReturnType<typeof solveSocket.useSolveSocket>);
    };
    syncSocket();

    const { rerender } = renderMain();
    await user.click(screen.getByTestId('send-image'));
    expect(screen.getByText('请根据附件中的题目进行求解。')).toBeInTheDocument();

    events = [{
      type: 'problem_extracted',
      problem: '设 $a+b=1$，证明 $a^2+b^2\\geq \\frac12$。',
    }];
    syncSocket();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MainColumn />
      </QueryClientProvider>,
    );

    expect(await screen.findByText((_, element) => Boolean(
      element?.classList.contains('math-text')
      && element.textContent?.includes('a+b=1')
    ))).toBeInTheDocument();
    expect(screen.queryByText('请根据附件中的题目进行求解。')).not.toBeInTheDocument();
  });
});
