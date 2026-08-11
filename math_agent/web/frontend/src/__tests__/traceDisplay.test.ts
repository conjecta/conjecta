import { describe, it, expect } from 'vitest';
import {
  buildTraceDisplay,
  humanizeAction,
  humanizeStage,
  lastRunningTool,
  parseAgentPayload,
  previewToolArgs,
  sanitizeThoughtText,
  tryExtractThought,
  type TraceDisplayItem,
} from '@/lib/traceDisplay';
import type { WsEvent } from '@/types/websocket';

const MULTI_JSON = `{"thought":"Current target: assess statement validity. The given problem as written appears suspect.","action":{"name":"think","args":{"text":"counterexample first"}}}
{"thought":"Current target: final response. The statement is false as written.","action":{"name":"conclude","args":{"answer":"该命题是假的。"}}}`;

describe('tryExtractThought', () => {
  it('parses complete JSON thought', () => {
    const raw = '{"thought":"Current target: final theorem.","action":{"name":"conclude"}}';
    expect(tryExtractThought(raw)).toBe('Current target: final theorem.');
  });

  it('parses partial streaming JSON', () => {
    const raw = '{"thought":"Current target: final theorem. We can prove';
    expect(tryExtractThought(raw)).toBe('Current target: final theorem. We can prove');
  });

  it('parses concatenated JSON objects without showing raw action blobs', () => {
    const parsed = parseAgentPayload(MULTI_JSON);
    expect(parsed.thought).toContain('assess statement validity');
    expect(parsed.thought).toContain('final response');
    expect(parsed.actionName).toBe('conclude');
    expect(parsed.thought).not.toContain('"action"');
    expect(sanitizeThoughtText(MULTI_JSON)).not.toContain('"action"');
  });
});

describe('buildTraceDisplay', () => {
  it('does not surface raw token JSON as visible items', () => {
    const events = [
      { type: 'stage_status', stage: 'thinking', message: 'Entering reasoning loop.' },
      { type: 'llm_start', label: 'Generating the next action...' },
      {
        type: 'token',
        content: '{"thought":"Current target: final theorem.","action":{"name":"conclude","args":{"answer":"proof"}}}',
      },
    ] as WsEvent[];

    const { items, isGenerating } = buildTraceDisplay(events);
    expect(items.some((item) => item.kind === 'event' && item.event.type === 'token')).toBe(false);
    expect(items.some((item) => item.kind === 'generating')).toBe(true);
    expect(isGenerating).toBe(true);
    const generating = items.find((item) => item.kind === 'generating');
    expect(generating && generating.kind === 'generating' ? generating.thoughtPreview : null)
      .toContain('Current target: final theorem.');
  });

  it('renders parsed step thoughts instead of token JSON', () => {
    const events = [
      { type: 'llm_start', label: 'Generating the next action...' },
      { type: 'token', content: '{"thought":"partial' },
      { type: 'step_start', step_num: 1, action: 'conclude' },
      {
        type: 'step',
        step_num: 1,
        action: 'conclude',
        thought: 'Current target: final theorem.',
        observation: 'Conclusion: proof',
        verified: true,
      },
      { type: 'done', summary: 'proof' },
    ] as WsEvent[];

    const { items, isGenerating } = buildTraceDisplay(events);
    expect(isGenerating).toBe(false);
    expect(items.some((item) => item.kind === 'generating')).toBe(false);
    const step = items.find((item) => item.kind === 'step');
    expect(step && step.kind === 'step' ? step.data.thought : null).toBe('Current target: final theorem.');
  });

  it('sanitizes raw multi-json thought blobs on step events', () => {
    const events = [
      {
        type: 'step',
        step_num: 1,
        action: 'conclude',
        thought: MULTI_JSON,
        observation: 'Conclusion: 该命题是假的。',
      },
    ] as WsEvent[];

    const { items } = buildTraceDisplay(events);
    const step = items.find((item) => item.kind === 'step');
    const thought = step && step.kind === 'step' ? step.data.thought : null;
    expect(thought).toContain('assess statement validity');
    expect(thought).toContain('final response');
    expect(thought).not.toContain('"action"');
  });

  it('tracks the latest stage for the status bar', () => {
    const events = [
      { type: 'stage_status', stage: 'analyzing', message: '正在理解问题…', ui: 'status_bar' },
      { type: 'stage_status', stage: 'preparing_knowledge', message: '正在检索相关先验知识…', ui: 'status_bar' },
      { type: 'stage_status', stage: 'thinking', message: '开始推理…', ui: 'status_bar' },
    ] as WsEvent[];

    const { activeStage, items } = buildTraceDisplay(events);
    expect(activeStage?.stage).toBe('thinking');
    expect(activeStage?.message).toBe('开始推理…');
    // status_bar stages update the sticky bar but stay out of the process log
    expect(items.filter((item) => item.kind === 'stage')).toHaveLength(0);
  });

  it('keeps timeline stage_status rows in the process log', () => {
    const events = [
      { type: 'stage_status', stage: 'warning', message: '需要联网核实，但当前搜索未返回结果。' },
    ] as WsEvent[];

    const { items, activeStage } = buildTraceDisplay(events);
    expect(activeStage?.stage).toBe('warning');
    expect(items.filter((item) => item.kind === 'stage')).toHaveLength(1);
  });

  it('collapses consecutive memory_retrieval events into one summary', () => {
    const events = [
      { type: 'stage_status', stage: 'preparing_knowledge', message: 'Retrieving relevant prior knowledge.' },
      { type: 'memory_retrieval', memory_id: 'f1', kind: 'fact', rank: 1 },
      { type: 'memory_retrieval', memory_id: 'f2', kind: 'fact', rank: 2 },
      { type: 'user_memory_retrieval', memory_id: 'u1', kind: 'user_note', rank: 3 },
      { type: 'memory_retrieval', memory_id: 't1', kind: 'trick', rank: 4 },
      { type: 'stage_status', stage: 'thinking', message: 'Entering reasoning loop.' },
    ] as WsEvent[];

    const { items } = buildTraceDisplay(events);
    const retrievals = items.filter((item) => item.kind === 'memory_retrieval');
    expect(retrievals).toHaveLength(1);
    expect(retrievals[0]).toMatchObject({ kind: 'memory_retrieval', count: 4 });
    expect(items.some((item) => item.kind === 'event' && item.event.type === 'memory_retrieval')).toBe(
      false,
    );
  });

  it('separates process items from the final answer', () => {
    const events = [
      { type: 'stage_status', stage: 'thinking', message: 'Entering reasoning loop.' },
      {
        type: 'step',
        step_num: 1,
        action: 'conclude',
        thought: 'apply maximum principle',
        observation: 'Conclusion: u >= 0',
      },
      { type: 'done', summary: 'u >= 0', final_answer: 'u >= 0' },
    ] as WsEvent[];

    const { processItems, answerItems, hasAnswer, activeStage } = buildTraceDisplay(events);
    expect(hasAnswer).toBe(true);
    expect(activeStage).toBeNull();
    expect(answerItems).toHaveLength(1);
    expect(processItems.every((item) => !(item.kind === 'event' && item.event.type === 'done'))).toBe(true);
    expect(processItems.some((item) => item.kind === 'step')).toBe(true);
  });

  it('keeps reviewer issues on failed steps for display', () => {
    const events = [
      {
        type: 'step',
        step_num: 1,
        action: 'conclude',
        thought: 'finish identity',
        observation: 'Conclusion: incomplete latex \\[\\frac{HD}{AD}+\\fr',
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

    const { items } = buildTraceDisplay(events);
    const step = items.find((item) => item.kind === 'step');
    expect(step && step.kind === 'step' ? step.data.verified : null).toBe(false);
    expect(step && step.kind === 'step' ? step.data.reviewIssues : null).toEqual([
      'critic: Proof cuts off mid-identity',
    ]);
  });

  it('keeps same-numbered steps from parallel research routes separate', () => {
    const events = [
      { type: 'step', step_num: 1, action: 'think', thought: 'route one', research_goal_id: 'lemma-a', research_attempt: 1 },
      { type: 'step', step_num: 1, action: 'think', thought: 'route two', research_goal_id: 'lemma-a', research_attempt: 2 },
    ] as WsEvent[];

    const { items } = buildTraceDisplay(events);
    const steps = items.filter((item) => item.kind === 'step');
    expect(steps).toHaveLength(2);
    expect(steps.map((item) => item.kind === 'step' ? item.data.thought : '')).toEqual([
      'route one',
      'route two',
    ]);
  });

  it('does not merge live tokens from parallel research routes', () => {
    const events = [
      { type: 'llm_start', label: 'route 1', research_goal_id: 'lemma-a', research_attempt: 1 },
      { type: 'token', content: '{"thought":"first route', research_goal_id: 'lemma-a', research_attempt: 1 },
      { type: 'llm_start', label: 'route 2', research_goal_id: 'lemma-a', research_attempt: 2 },
      { type: 'token', content: '{"thought":"second route', research_goal_id: 'lemma-a', research_attempt: 2 },
    ] as WsEvent[];

    const { items, isGenerating } = buildTraceDisplay(events);
    const previews = items
      .filter((item) => item.kind === 'generating')
      .map((item) => item.kind === 'generating' ? item.thoughtPreview : '');
    expect(isGenerating).toBe(true);
    expect(previews).toEqual(['first route', 'second route']);
  });

  it('hides turn_started control events from the visible trace', () => {
    const events = [
      { type: 'session', session_id: 's1' },
      { type: 'turn_started', turn_id: 't1', conversation_id: 'c1', problem: 'Prove P' },
      { type: 'stage_status', stage: 'thinking', message: 'Entering reasoning loop.' },
    ] as WsEvent[];

    const { items } = buildTraceDisplay(events);
    expect(items.every((item) => item.kind !== 'event' || item.event.type !== 'turn_started')).toBe(true);
    expect(items.some((item) => item.kind === 'stage')).toBe(true);
  });
});

describe('tool call display', () => {
  const toolItem = (items: TraceDisplayItem[]) =>
    items.find((item) => item.kind === 'tool');

  it('builds a one-line args preview from an agent JSON payload', () => {
    const input = JSON.stringify({
      thought: 'check the lemma',
      action: { name: 'lean_check', args: { code: 'theorem foo : True := trivial' } },
    });
    expect(previewToolArgs(input)).toBe('code: theorem foo : True := trivial');
  });

  it('summarizes plain object input and truncates long values', () => {
    expect(previewToolArgs({ query: 'Catalan numbers' })).toBe('query: Catalan numbers');
    const long = previewToolArgs({ code: 'x'.repeat(300) });
    expect(long.length).toBeLessThanOrEqual(140);
    expect(long.endsWith('…')).toBe(true);
  });

  it('returns an empty preview for empty or unhelpful input', () => {
    expect(previewToolArgs(null)).toBe('');
    expect(previewToolArgs(undefined)).toBe('');
    expect(previewToolArgs({})).toBe('');
  });

  it('carries args preview and duration from tool_start to tool_done', () => {
    const events = [
      { type: 'tool_start', tool: 'lean_check', step_num: 1, input: { code: 'trivial' }, _ts: 1000 },
      { type: 'tool_done', tool: 'lean_check', step_num: 1, output: 'ok', success: true, _ts: 2600 },
    ] as unknown as WsEvent[];

    const { processItems } = buildTraceDisplay(events);
    const item = toolItem(processItems);
    expect(item?.kind).toBe('tool');
    if (item?.kind !== 'tool') return;
    expect(item.data.running).toBe(false);
    expect(item.data.argsPreview).toBe('code: trivial');
    expect(item.data.durationMs).toBe(1600);
  });

  it('prefers a server-provided duration_seconds over client timestamps', () => {
    const events = [
      { type: 'tool_start', tool: 'search', step_num: 1, _ts: 1000 },
      { type: 'tool_done', tool: 'search', step_num: 1, duration_seconds: 0.4, _ts: 9000 },
    ] as unknown as WsEvent[];

    const item = toolItem(buildTraceDisplay(events).processItems);
    if (item?.kind !== 'tool') throw new Error('expected tool item');
    expect(item.data.durationMs).toBe(400);
  });

  it('reports the latest still-running tool for the status bar', () => {
    const running = buildTraceDisplay([
      { type: 'tool_start', tool: 'lean_check', step_num: 1 },
    ] as unknown as WsEvent[]);
    expect(lastRunningTool(running.processItems)).toBe('lean_check');

    const finished = buildTraceDisplay([
      { type: 'tool_start', tool: 'lean_check', step_num: 1 },
      { type: 'tool_done', tool: 'lean_check', step_num: 2 },
    ] as unknown as WsEvent[]);
    expect(lastRunningTool(finished.processItems)).toBeNull();

    expect(lastRunningTool([])).toBeNull();
  });

  it('updates a running tool card from tool_progress events', () => {
    const events = [
      { type: 'tool_start', tool: 'prove_by_lemmas', step_num: 1, _ts: 1000 },
      { type: 'tool_progress', tool: 'prove_by_lemmas', step_num: 1, elapsed_seconds: 20 },
      {
        type: 'tool_progress',
        tool: 'prove_by_lemmas',
        step_num: 1,
        elapsed_seconds: 30,
        detail: '引理 1/2 `step_one` 验证通过',
      },
    ] as unknown as WsEvent[];

    const item = toolItem(buildTraceDisplay(events).processItems);
    if (item?.kind !== 'tool') throw new Error('expected tool item');
    expect(item.data.running).toBe(true);
    expect(item.data.elapsedSeconds).toBe(30);
    expect(item.data.progressMessage).toBe('引理 1/2 `step_one` 验证通过');
  });

  it('ignores tool_progress for unknown or finished tools', () => {
    const events = [
      { type: 'tool_progress', tool: 'lean_check', step_num: 9, elapsed_seconds: 10 },
      { type: 'tool_start', tool: 'lean_check', step_num: 1 },
      { type: 'tool_done', tool: 'lean_check', step_num: 1, output: 'ok', success: true },
      { type: 'tool_progress', tool: 'lean_check', step_num: 1, elapsed_seconds: 99 },
    ] as unknown as WsEvent[];

    const { processItems } = buildTraceDisplay(events);
    const toolItems = processItems.filter((item) => item.kind === 'tool');
    expect(toolItems).toHaveLength(1);
    const item = toolItems[0];
    if (item.kind !== 'tool') throw new Error('expected tool item');
    expect(item.data.running).toBe(false);
    expect(item.data.elapsedSeconds).toBeUndefined();
  });

  it('prefers the backend args_preview string and keeps the raw payload', () => {
    const events = [
      {
        type: 'tool_start',
        tool: 'compute',
        step_num: 1,
        args_preview: JSON.stringify({ code: 'print(40 + 2)' }),
      },
      { type: 'tool_done', tool: 'compute', step_num: 1, output: '42', success: true },
    ] as unknown as WsEvent[];

    const item = toolItem(buildTraceDisplay(events).processItems);
    if (item?.kind !== 'tool') throw new Error('expected tool item');
    expect(item.data.argsPreview).toBe('code: print(40 + 2)');
    expect(item.data.argsRaw).toBe(JSON.stringify({ code: 'print(40 + 2)' }));
    expect(item.data.output).toBe('42');
  });

  it('pairs claim_check tool_start/tool_done keyed by the string step_num', () => {
    const events = [
      {
        type: 'tool_start',
        tool: 'compute',
        step_num: 'claim_check',
        args_preview: JSON.stringify({ code: 'sympy.check()' }),
      },
      { type: 'tool_done', tool: 'compute', step_num: 'claim_check', output: 'ok', success: true },
    ] as unknown as WsEvent[];

    const { processItems } = buildTraceDisplay(events);
    const tools = processItems.filter((item) => item.kind === 'tool');
    expect(tools).toHaveLength(1);
    const item = tools[0];
    if (item.kind !== 'tool') throw new Error('expected tool item');
    expect(item.data.running).toBe(false);
    expect(item.data.output).toBe('ok');
    expect(item.data.argsRaw).toContain('sympy.check()');
  });

  // The UI is Chinese throughout; a leaked English stage name or tool id reads
  // as a bug. These lock the label coverage and the neutral fallback.
  it('labels every stage the backend emits in Chinese', () => {
    const stages = [
      'accepting', 'analyzing', 'attachment', 'claim_check', 'finalizing',
      'learning', 'mid_verify', 'planning', 'preparing_knowledge', 'reducing',
      'refining', 'researching', 'reviewing', 'synthesizing', 'thinking',
      'warning',
    ];
    for (const stage of stages) {
      const label = humanizeStage(stage);
      expect(label, `stage ${stage}`).not.toMatch(/[A-Za-z]/);
    }
  });

  it('falls back to a neutral Chinese phrase for an unmapped stage', () => {
    expect(humanizeStage('some_new_stage')).toBe('处理中');
  });

  it('labels every registered tool in Chinese', () => {
    const tools = [
      'add_material', 'compute', 'conclude', 'fetch_url', 'find_related',
      'formalize', 'plot_figure', 'prove_by_lemmas', 'read_sources',
      'relate_knowledge', 'search', 'search_arxiv', 'search_knowledge',
      'search_materials', 'search_mathlib', 'search_scholar', 'search_web',
      'set_goal', 'tactic_search', 'think',
    ];
    for (const tool of tools) {
      // Lean and arXiv are proper nouns and stay Latin; everything else is CJK.
      const label = humanizeAction(tool);
      expect(label, `tool ${tool}`).not.toBe(tool.replace(/_/g, ' '));
    }
  });
});
