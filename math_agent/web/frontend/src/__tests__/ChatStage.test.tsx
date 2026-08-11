// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ChatStage } from '../components/ChatStage';
import type { WsEvent } from '../types/websocket';

/** Expand the collapsed 推理与验证 process log. */
function expandProcessTrace() {
  fireEvent.click(screen.getByRole('button', { name: /推理与验证/ }));
}

describe('ChatStage', () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('shows a minimal hero with the centered composer slot and no example prompts', () => {
    render(
      <ChatStage
        events={[]}
        connectionError={null}
        onDismissError={() => {}}
        composerSlot={<div data-testid="hero-composer">composer</div>}
      />,
    );

    expect(screen.getByRole('heading', { name: /证明，或\s*证伪\s*。/ })).toBeInTheDocument();
    expect(screen.getByTestId('hero-composer')).toBeInTheDocument();
    expect(screen.queryByText('试试这些问题')).not.toBeInTheDocument();
    expect(screen.queryByText('最新数学动态')).not.toBeInTheDocument();
  });

  it('keeps the process log collapsed behind a disclosure while events stream', () => {
    const events = [
      { type: 'signal', signal: 'PLAN_COMPLETED', message: 'Plan ready' },
      { type: 'goal_evaluation', score: 0.82, reasoning: 'The proof has a verified final step.' },
      { type: 'checkpoint', checkpoint_id: 'session-1', resumable: true, reason: 'Step completed.' },
      { type: 'mystery_event', message: 'Still visible' },
    ] as unknown as WsEvent[];

    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} />);

    // Collapsed by default: signal, goal evaluation, checkpoint, and unknown
    // events are kept, just behind the disclosure.
    expect(screen.queryByText(/PLAN_COMPLETED/i)).not.toBeInTheDocument();
    expandProcessTrace();
    expect(screen.getByText(/PLAN_COMPLETED/i)).toBeInTheDocument();
    expect(screen.getByText(/The proof has a verified final step/i)).toBeInTheDocument();
    expect(screen.getByText(/Checkpoint saved/i)).toBeInTheDocument();
    expect(screen.getByText(/mystery_event/i)).toBeInTheDocument();
  });

  it('renders a durable human decision card and submits approval', () => {
    const onHumanDecision = vi.fn();
    const event = {
      type: 'human_input_required',
      checkpoint_id: 'session-1',
      request_id: 'hitl-1',
      kind: 'plan_review',
      stage: 'planning',
      question: '研究计划已经生成。是否继续？',
      allowed_decisions: ['approve', 'reject', 'edit'],
      resumable: true,
      details: {
        proof_graph: {
          goals: [{ id: 'g1', statement: '先证明引理 A', depends_on: [] }],
        },
      },
    } as WsEvent;

    render(
      <ChatStage
        events={[event]}
        connectionError={null}
        onDismissError={() => {}}
        status="waiting_human"
        onHumanDecision={onHumanDecision}
      />,
    );

    expect(screen.getByRole('region', { name: '需要你的决定' })).toBeInTheDocument();
    expect(screen.getByText(/先证明引理 A/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /按此方案开始研究/ }));
    expect(onHumanDecision).toHaveBeenCalledWith(event, 'approve', '');
  });

  it('keeps plan edits progressive and submits only after feedback is provided', () => {
    const onHumanDecision = vi.fn();
    const event = {
      type: 'human_input_required',
      checkpoint_id: 'session-2',
      request_id: 'hitl-2',
      kind: 'plan_review',
      stage: 'planning',
      question: '检查这条证明路线。',
      allowed_decisions: ['approve', 'reject', 'edit'],
      resumable: true,
      details: {
        proof_graph: {
          root_id: 'root',
          goals: [
            { id: 'root', statement: '证明主结论', depends_on: ['g1'] },
            { id: 'g1', statement: '建立关键引理', depends_on: [] },
          ],
        },
      },
    } as WsEvent;

    render(
      <ChatStage
        events={[event]}
        connectionError={null}
        onDismissError={() => {}}
        status="waiting_human"
        onHumanDecision={onHumanDecision}
      />,
    );

    expect(screen.queryByPlaceholderText(/先尝试组合证明/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /调整方案/ }));
    const textarea = screen.getByPlaceholderText(/先尝试组合证明/);
    fireEvent.change(textarea, { target: { value: '先处理关键引理。' } });
    fireEvent.click(screen.getByRole('button', { name: '提交修改方案' }));
    expect(onHumanDecision).toHaveBeenCalledWith(event, 'edit', '先处理关键引理。');
  });

  it('shows thought text and hides raw token JSON during generation', () => {
    const events = [
      { type: 'stage_status', stage: 'thinking', message: 'Entering reasoning loop.' },
      { type: 'llm_start', label: 'Generating the next action...' },
      {
        type: 'token',
        content: '{"thought":"Current target: final theorem.","action":{"name":"conclude"}}',
      },
    ] as WsEvent[];

    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} status="streaming" />);

    expandProcessTrace();
    expect(screen.getByText(/Current target: final theorem/i)).toBeInTheDocument();
    expect(screen.queryByText(/"action":\{"name":"conclude"\}/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/推理中/i).length).toBeGreaterThan(0);
  });

  it('beautifies concatenated JSON thought blobs instead of showing raw action JSON', () => {
    const multi = `{"thought":"Current target: assess statement validity.","action":{"name":"think","args":{"text":"check counterexample"}}}
{"thought":"Current target: final response. The statement is false.","action":{"name":"conclude","args":{"answer":"该命题是假的。"}}}`;
    const events = [
      {
        type: 'step',
        step_num: 1,
        action: 'conclude',
        thought: multi,
        observation: 'Conclusion: 该命题是假的。',
      },
    ] as WsEvent[];

    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} />);

    expandProcessTrace();
    expect(screen.getByText(/assess statement validity/i)).toBeInTheDocument();
    expect(screen.getByText(/final response/i)).toBeInTheDocument();
    expect(screen.getByText(/给出结论/i)).toBeInTheDocument();
    expect(screen.queryByText(/"action"/i)).not.toBeInTheDocument();
  });

  it('renders completed step thoughts from step events', () => {
    const events = [
      { type: 'step_start', step_num: 1, action: 'conclude' },
      {
        type: 'step',
        step_num: 1,
        action: 'conclude',
        thought: 'Current target: final theorem.',
        observation: 'Conclusion: proof complete',
      },
    ] as WsEvent[];

    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} />);

    expandProcessTrace();
    expect(screen.getAllByText(/Current target: final theorem/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/proof complete/i)).toBeInTheDocument();
  });

  it('shows reviewer issues when a step needs review', () => {
    const events = [
      {
        type: 'step',
        step_num: 1,
        action: 'conclude',
        thought: 'finish identity',
        observation: 'Conclusion: incomplete',
        verified: false,
        reviews: [
          {
            reviewer: 'critic',
            verdict: 'FAIL',
            issues: ['Proof cuts off mid-identity'],
          },
        ],
      },
    ] as WsEvent[];

    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} />);

    expandProcessTrace();
    expect(screen.getByText(/需复核/i)).toBeInTheDocument();
    expect(screen.getByText(/critic: Proof cuts off mid-identity/i)).toBeInTheDocument();
  });

  it('auto-collapses the thinking process after the answer arrives but keeps it expandable', async () => {
    const events = [
      { type: 'stage_status', stage: 'thinking', message: 'Entering reasoning loop.' },
      {
        type: 'step',
        step_num: 1,
        action: 'conclude',
        thought: 'Current target: final theorem.',
        observation: 'Conclusion: proof complete',
      },
      { type: 'done', summary: 'u(x) ≥ 0', final_answer: 'u(x) ≥ 0' },
    ] as WsEvent[];

    const { userEvent } = await import('@testing-library/user-event');
    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} status="done" />);

    expect(screen.getByText(/u\(x\) ≥ 0/i)).toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: /推理与验证/i });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    // Collapsed row carries a one-line summary plus the step count.
    expect(toggle).toHaveTextContent(/给出结论/);
    expect(toggle).toHaveTextContent(/2 步/);
    expect(screen.queryByText(/Entering reasoning loop/i)).not.toBeInTheDocument();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/Entering reasoning loop/i)).toBeInTheDocument();
  });

  it('shows research goal progress and final verification semantics', async () => {
    const events = [
      { type: 'session', session_id: 'sess-live' },
      {
        type: 'capability_health',
        capabilities: { sagemath: { status: 'connected', tool_count: 2 } },
      },
      {
        type: 'proof_graph',
        proof_graph: {
          root_id: 'root',
          active_goal_id: 'compactness',
          goals: [
            { id: 'compactness', statement: 'Prove the compactness lemma.', status: 'proved', depends_on: [] },
            { id: 'root', statement: 'Root theorem', status: 'pending', depends_on: ['compactness'] },
          ],
        },
      },
      {
        type: 'research_attempt_summary',
        goal_id: 'compactness',
        attempt_count: 2,
        accepted_count: 2,
        failed_count: 0,
        quorum_required: 2,
        quorum_met: true,
        cross_check: 'agree',
      },
      {
        type: 'research_goal',
        goal_id: 'compactness',
        statement: 'Prove the compactness lemma.',
        status: 'proved',
        verification_status: 'reviewed',
      },
      {
        type: 'done',
        final_answer: 'The theorem follows from compactness.',
        strategy: 'research',
        verification_status: 'reviewed',
        verification_issues: [],
      },
    ] as WsEvent[];

    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} status="done" />);

    // Research mode is gone: no proof-graph canvas and no per-goal grouping,
    // just the one flat process log every solve uses.
    expect(screen.queryByRole('region', { name: '证明图' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('dag-canvas')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '主线过程' })).not.toBeInTheDocument();
    // The answer card still carries the run's verification semantics.
    expect(screen.getByText('深度研究')).toBeInTheDocument();
    expect(screen.getAllByText('审阅通过').length).toBeGreaterThan(0);

    expandProcessTrace();
    expect(screen.getByRole('region', { name: '研究能力状态' })).toBeInTheDocument();
  });

  it('keeps proof_graph events as metadata without switching the trace UI', () => {
    const events = [
      {
        type: 'proof_graph',
        proof_graph: {
          root_id: 'root',
          active_goal_id: 'lemma-a',
          goals: [
            { id: 'lemma-a', statement: '证明引理 A', status: 'in_progress', depends_on: [] },
            { id: 'root', statement: '主定理', status: 'pending', depends_on: ['lemma-a'] },
          ],
        },
      },
      { type: 'step', step_num: 1, action: 'think', thought: 'a draft thought' },
    ] as WsEvent[];

    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} status="streaming" />);

    // One flat log; the graph is not rendered as goal groups or a canvas.
    expect(screen.queryByRole('button', { name: '证明引理 A' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '主定理' })).not.toBeInTheDocument();
    expandProcessTrace();
    expect(screen.getByText('a draft thought')).toBeInTheDocument();
  });

  it('hides internal reviewer roles and exception names in verification notices', () => {
    const events = [{
      type: 'done',
      final_answer: 'A best-effort answer.',
      verification_status: 'best_effort',
      verification_issues: [
        'Reviewer critic was unavailable: PermissionDeniedError.',
        'Reviewer fidelity was unavailable: PermissionDeniedError.',
      ],
    }] as WsEvent[];

    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} status="done" />);

    expect(screen.getByText('阶段性研究结果')).toBeInTheDocument();
    expect(screen.getByText('尚未闭合')).toBeInTheDocument();
    expect(screen.getAllByText('· 部分自动审阅暂不可用，当前答案未完成全部复核。')).toHaveLength(1);
    expect(screen.queryByText(/critic|fidelity|PermissionDeniedError/i)).not.toBeInTheDocument();
  });

  it('renders unreviewed as a non-success terminal state', () => {
    const events = [{
      type: 'done',
      final_answer: 'An easy answer.',
      verification_status: 'unreviewed',
    }] as WsEvent[];

    render(<ChatStage events={events} connectionError={null} onDismissError={() => {}} status="done" />);

    expect(screen.getByText('阶段性研究结果')).toBeInTheDocument();
    expect(screen.getByText('审阅跳过')).toBeInTheDocument();
    expect(screen.queryByText('最终答案')).not.toBeInTheDocument();
  });
});

describe('ChatStage solving status bar', () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('shows the latest stage message with elapsed time and no duplicate stop', () => {
    vi.useFakeTimers();
    render(
      <ChatStage
        events={[{ type: 'stage_status', stage: 'thinking', message: '分析条件' } as WsEvent]}
        connectionError={null}
        onDismissError={() => {}}
        status="streaming"
      />,
    );

    // The message appears in the sticky status bar while the trace row stays
    // collapsed behind the disclosure.
    expect(screen.getAllByText('分析条件').length).toBeGreaterThan(0);
    expect(screen.getByText('0s')).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByText('3s')).toBeInTheDocument();

    // Stop lives only in the composer now — the status bar must not add a second one.
    expect(screen.queryByRole('button', { name: /停止/ })).not.toBeInTheDocument();
  });

  it('falls back to the running tool name, then to 正在思考…', () => {
    const { rerender } = render(
      <ChatStage
        events={[{ type: 'tool_start', tool: 'lean_check', step_num: 1 } as unknown as WsEvent]}
        connectionError={null}
        onDismissError={() => {}}
        status="streaming"
      />,
    );
    // Appears in the status bar and again in the collapsed trace summary.
    expect(screen.getAllByText('正在调用 Lean 验证').length).toBeGreaterThan(0);

    rerender(
      <ChatStage
        events={[{ type: 'llm_start', label: 'drafting' } as WsEvent]}
        connectionError={null}
        onDismissError={() => {}}
        status="streaming"
      />,
    );
    expect(screen.getByText('正在思考…')).toBeInTheDocument();
  });

  it('shows a search-specific status while a search tool runs', () => {
    render(
      <ChatStage
        events={[{ type: 'tool_start', tool: 'search_arxiv', step_num: 1 } as unknown as WsEvent]}
        connectionError={null}
        onDismissError={() => {}}
        status="streaming"
      />,
    );
    expect(screen.getByText('正在搜索：搜索 arXiv 文献…')).toBeInTheDocument();
  });

  it('shows a background solving banner with elapsed time', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-26T14:00:30Z'));
    render(
      <ChatStage
        events={[]}
        connectionError={null}
        onDismissError={() => {}}
        status="background"
        backgroundSessionId="sess-abcdef"
        backgroundStartedAt="2026-07-26T14:00:00Z"
      />,
    );

    expect(screen.getByText(/后台求解中/)).toBeInTheDocument();
    expect(screen.getByText(/已用时 30s/)).toBeInTheDocument();
  });

  it('renders tool calls as collapsed cards that expand to show output', () => {
    const events = [
      {
        type: 'tool_start',
        tool: 'lean_check',
        step_num: 1,
        input: { code: 'theorem foo : True := trivial' },
        _ts: 1000,
      },
      {
        type: 'tool_done',
        tool: 'lean_check',
        step_num: 1,
        output: '验证通过：无错误',
        success: true,
        _ts: 2600,
      },
    ] as unknown as WsEvent[];

    render(
      <ChatStage
        events={events}
        connectionError={null}
        onDismissError={() => {}}
        status="streaming"
      />,
    );

    expandProcessTrace();
    expect(screen.getByText('Lean 验证')).toBeInTheDocument();
    expect(screen.getByText('code: theorem foo : True := trivial')).toBeInTheDocument();
    expect(screen.getByText('1.6s')).toBeInTheDocument();
    expect(screen.queryByText('验证通过：无错误')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Lean 验证/ }));
    expect(screen.getByText('验证通过：无错误')).toBeInTheDocument();
  });
});
