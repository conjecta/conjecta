import type { WsEvent } from '@/types/websocket';
import { summarizeArgsPreview } from '@/lib/toolRender';

export interface StageInfo {
  stage: string;
  message?: string;
}

export interface StepDisplay {
  stepNum: number;
  action: string;
  thought?: string;
  observation?: string;
  verified?: boolean | null;
  reviewIssues?: string[];
}

export interface ToolDisplay {
  stepNum: number;
  tool: string;
  running: boolean;
  output?: string;
  success?: boolean;
  /** One-line summary of the tool arguments (from tool_start input). */
  argsPreview?: string;
  /** Raw args_preview payload from tool_start (full JSON for compute). */
  argsRaw?: string;
  /** Client-side arrival time (ms) of the tool_start event, used for duration. */
  startedAt?: number;
  /** Wall-clock duration of the call, when both endpoints are known. */
  durationMs?: number;
  /** Elapsed seconds reported by tool_progress heartbeats while running. */
  elapsedSeconds?: number;
  /** Latest structured progress line (tool_progress `detail`), e.g. per-lemma status. */
  progressMessage?: string;
}

export type TraceDisplayItem =
  | { kind: 'stage'; id: string; stage: string; message?: string }
  | { kind: 'generating'; id: string; label: string; charCount: number; thoughtPreview?: string }
  | { kind: 'step'; id: string; data: StepDisplay }
  | { kind: 'tool'; id: string; data: ToolDisplay }
  | {
      kind: 'memory_retrieval';
      id: string;
      count: number;
    }
  | { kind: 'event'; id: string; event: WsEvent };

export interface TraceDisplayState {
  items: TraceDisplayItem[];
  processItems: TraceDisplayItem[];
  answerItems: TraceDisplayItem[];
  activeStage: StageInfo | null;
  isGenerating: boolean;
  hasAnswer: boolean;
}

export interface ParsedAgentPayload {
  thought: string | null;
  actionName: string | null;
  actionArgs: Record<string, unknown> | null;
}

function eventText(data: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === 'string' && value) return value;
  }
  return '';
}

export function humanizeStage(stage: string): string {
  const key = stage.toLowerCase().replace(/\s+/g, '_');
  const labels: Record<string, string> = {
    planning: '规划',
    claim_check: '命题审查',
    solving: '求解',
    reviewer: '审查',
    reviewer_panel: '审查',
    analyzing: '分析中',
    preparing_knowledge: '准备知识库',
    thinking: '推理中',
    reviewing: '审核中',
    accepting: '置信度较高，跳过审核',
    finalizing: '整理答案',
    learning: '学习中',
    refining: '提炼知识',
    warning: '警告',
    attachment: '处理附件',
    reducing: '拆解研究目标',
    researching: '深度研究中',
    synthesizing: '汇总研究结论',
    mid_verify: '验证中间结论',
  };
  // Unmapped stages used to render as SHOUTED ENGLISH (e.g. MID VERIFY), which
  // reads as a leaked internal name next to the Chinese labels. Fall back to a
  // neutral Chinese phrase instead; the raw stage stays available via title.
  return labels[key] || '处理中';
}

function unescapeJsonString(raw: string): string {
  try {
    return JSON.parse(`"${raw}"`) as string;
  } catch {
    return raw.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
  }
}

function looksLikeJsonBlob(text: string): boolean {
  const trimmed = text.trim();
  return trimmed.startsWith('{') && (trimmed.includes('"thought"') || trimmed.includes('"action"'));
}

/** Split concatenated JSON objects like `{...}\n{...}` into parseable chunks. */
export function splitJsonObjects(raw: string): string[] {
  const text = raw.trim();
  if (!text) return [];
  const chunks: string[] = [];
  let depth = 0;
  let start = -1;
  let inString = false;
  let escape = false;

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      if (escape) {
        escape = false;
      } else if (ch === '\\') {
        escape = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === '{') {
      if (depth === 0) start = i;
      depth += 1;
    } else if (ch === '}') {
      depth -= 1;
      if (depth === 0 && start >= 0) {
        chunks.push(text.slice(start, i + 1));
        start = -1;
      }
    }
  }
  return chunks;
}

export function parseAgentPayload(raw: string): ParsedAgentPayload {
  const empty: ParsedAgentPayload = { thought: null, actionName: null, actionArgs: null };
  const trimmed = raw.trim();
  if (!trimmed) return empty;

  const thoughts: string[] = [];
  let actionName: string | null = null;
  let actionArgs: Record<string, unknown> | null = null;

  const tryObject = (obj: unknown) => {
    if (!obj || typeof obj !== 'object') return;
    const data = obj as Record<string, unknown>;
    if (typeof data.thought === 'string' && data.thought.trim()) {
      thoughts.push(data.thought.trim());
    }
    const action = data.action;
    if (action && typeof action === 'object') {
      const act = action as Record<string, unknown>;
      if (typeof act.name === 'string' && act.name.trim()) {
        actionName = act.name.trim();
      }
      if (act.args && typeof act.args === 'object') {
        actionArgs = act.args as Record<string, unknown>;
      }
    }
  };

  try {
    tryObject(JSON.parse(trimmed));
  } catch {
    for (const chunk of splitJsonObjects(trimmed)) {
      try {
        tryObject(JSON.parse(chunk));
      } catch {
        /* ignore malformed chunk */
      }
    }
  }

  if (!thoughts.length) {
    const closed = trimmed.match(/"thought"\s*:\s*"((?:\\.|[^"\\])*)"/g);
    if (closed) {
      for (const match of closed) {
        const inner = match.match(/"thought"\s*:\s*"((?:\\.|[^"\\])*)"/);
        if (inner) thoughts.push(unescapeJsonString(inner[1]));
      }
    } else {
      const partial = trimmed.match(/"thought"\s*:\s*"((?:\\.|[^"\\])*)$/);
      if (partial) thoughts.push(unescapeJsonString(partial[1]));
    }
  }

  if (!actionName) {
    const nameMatch = trimmed.match(/"name"\s*:\s*"([^"\\]+)"/);
    if (nameMatch) actionName = nameMatch[1];
  }

  return {
    thought: thoughts.length ? thoughts.join('\n\n') : null,
    actionName,
    actionArgs,
  };
}

export function tryExtractThought(raw: string): string | null {
  return parseAgentPayload(raw).thought;
}

export function sanitizeThoughtText(raw: string | undefined): string | undefined {
  if (!raw) return undefined;
  if (!looksLikeJsonBlob(raw)) return raw;
  return tryExtractThought(raw) || undefined;
}

export function formatActionArgs(args: Record<string, unknown> | null | undefined): string {
  if (!args) return '';
  const entries = Object.entries(args);
  if (!entries.length) return '';
  return entries
    .map(([key, value]) => {
      if (typeof value === 'string') return `${key}: ${value}`;
      try {
        return `${key}: ${JSON.stringify(value)}`;
      } catch {
        return `${key}: ${String(value)}`;
      }
    })
    .join('\n');
}

export function formatReviewIssues(reviews: unknown): string[] {
  if (!Array.isArray(reviews)) return [];
  const issues: string[] = [];
  for (const review of reviews) {
    if (!review || typeof review !== 'object') continue;
    const data = review as Record<string, unknown>;
    const reviewer = typeof data.reviewer === 'string' ? data.reviewer : 'reviewer';
    const verdict = typeof data.verdict === 'string' ? data.verdict.toUpperCase() : '';
    if (verdict && verdict !== 'FAIL') continue;
    const rawIssues = Array.isArray(data.issues) ? data.issues : [];
    for (const issue of rawIssues) {
      if (typeof issue === 'string' && issue.trim()) {
        issues.push(`${reviewer}: ${issue.trim()}`);
      }
    }
  }
  return issues;
}

export function humanizeAction(action: string): string {
  const labels: Record<string, string> = {
    think: '思考',
    conclude: '给出结论',
    set_goal: '设定目标',
    formalize: '形式化',
    lean_check: 'Lean 验证',
    search_mathlib: '搜索 Mathlib',
    search_knowledge: '搜索知识库',
    search: '搜索网页',
    searching: '搜索网页',
    search_arxiv: '搜索 arXiv 文献',
    search_scholar: '搜索学术文献',
    fetch_url: '抓取网页',
    read_sources: '读取资料',
    search_materials: '搜索材料',
    search_web: '搜索网页',
    compute: '计算验证',
    plot_figure: '绘制图形',
    tactic_search: '搜索证明策略',
    prove_by_lemmas: '逐引理证明',
    find_related: '查找相关内容',
    relate_knowledge: '关联知识',
    add_material: '添加材料',
  };
  return labels[action] || action.replace(/_/g, ' ');
}

/** Client-side arrival stamp added by useSolveSocket (`_ts`, ms since epoch). */
function eventTimestamp(data: Record<string, unknown>): number | undefined {
  return typeof data._ts === 'number' && Number.isFinite(data._ts) ? data._ts : undefined;
}

const ARGS_PREVIEW_MAX = 140;

function truncateLine(text: string, max = ARGS_PREVIEW_MAX): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** One-line args summary for a tool card, from the tool_start `input` payload. */
export function previewToolArgs(input: unknown): string {
  if (input == null) return '';
  if (typeof input === 'string') {
    const parsed = parseAgentPayload(input);
    const formatted = formatActionArgs(parsed.actionArgs);
    if (formatted) return truncateLine(formatted);
    if (parsed.thought) return truncateLine(parsed.thought);
    return looksLikeJsonBlob(input) ? '' : truncateLine(input);
  }
  if (typeof input === 'object' && !Array.isArray(input)) {
    const data = input as Record<string, unknown>;
    const args = data.args && typeof data.args === 'object'
      ? (data.args as Record<string, unknown>)
      : data;
    const formatted = formatActionArgs(args);
    return formatted ? truncateLine(formatted) : '';
  }
  try {
    return truncateLine(JSON.stringify(input));
  } catch {
    return '';
  }
}

/** Name of the most recent tool call still running, for the status bar fallback. */
export function lastRunningTool(items: TraceDisplayItem[]): string | null {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === 'tool') return item.data.running ? item.data.tool : null;
  }
  return null;
}

/** One-line Chinese summary of the newest trace item, for the collapsed
 * process log. Char counts and raw English labels are deliberately dropped —
 * the collapsed row should read as status, not as a debug feed. */
export function latestTraceSummary(items: TraceDisplayItem[]): string {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === 'stage') return humanizeStage(item.stage);
    if (item.kind === 'generating') return '正在规划下一步';
    if (item.kind === 'tool') {
      return item.data.running
        ? `正在调用 ${humanizeAction(item.data.tool)}`
        : `已调用 ${humanizeAction(item.data.tool)}`;
    }
    if (item.kind === 'step') {
      return `第 ${item.data.stepNum} 步 · ${humanizeAction(item.data.action)}`;
    }
    if (item.kind === 'memory_retrieval') return `已检索 ${item.count} 条记忆`;
  }
  return '';
}

export function buildTraceDisplay(events: WsEvent[]): TraceDisplayState {
  const items: TraceDisplayItem[] = [];
  let activeStage: StageInfo | null = null;
  let isGenerating = false;
  const generatingStates = new Map<string, {
    id: string;
    label: string;
    tokenBuffer: string;
  }>();

  const stepIndex = new Map<string, number>();
  const toolIndex = new Map<string, number>();

  const eventScope = (data: Record<string, unknown>) => {
    const goal = eventText(data, 'research_goal_id');
    const attempt = typeof data.research_attempt === 'number' ? data.research_attempt : 0;
    return goal ? `${goal}-${attempt}` : 'main';
  };

  const scopedKey = (data: Record<string, unknown>, stepNum: number) =>
    `${eventScope(data)}-${stepNum}`;

  const upsertGenerating = (scope: string) => {
    const state = generatingStates.get(scope);
    if (!state) return;
    const parsed = parseAgentPayload(state.tokenBuffer);
    const existing = items.findIndex(
      (item) => item.kind === 'generating' && item.id === state.id,
    );
    const next: TraceDisplayItem = {
      kind: 'generating',
      id: state.id,
      label: state.label,
      charCount: state.tokenBuffer.length,
      thoughtPreview: parsed.thought || undefined,
    };
    if (existing >= 0) items[existing] = next;
    else items.push(next);
  };

  const clearGenerating = (scope?: string) => {
    const ids = scope
      ? [generatingStates.get(scope)?.id].filter((id): id is string => Boolean(id))
      : Array.from(generatingStates.values(), (state) => state.id);
    if (scope) generatingStates.delete(scope);
    else generatingStates.clear();
    for (let index = items.length - 1; index >= 0; index -= 1) {
      if (items[index].kind === 'generating' && ids.includes(items[index].id)) {
        items.splice(index, 1);
      }
    }
    isGenerating = generatingStates.size > 0;
  };

  let hasAnswer = false;

  events.forEach((event, index) => {
    const data = event as Record<string, unknown>;
    switch (event.type) {
      case 'stage_status': {
        clearGenerating(eventScope(data));
        const stage = eventText(data, 'stage');
        const message = eventText(data, 'message') || undefined;
        if (stage) activeStage = { stage, message };
        // Pipeline bookkeeping updates the sticky status bar only — keep the
        // process log for thinking / tools / steps (closer to Kimi Code TUI).
        const ui = eventText(data, 'ui') || 'timeline';
        if (ui !== 'status_bar') {
          items.push({
            kind: 'stage',
            id: `stage-${index}`,
            stage,
            message,
          });
        }
        break;
      }
      case 'llm_start': {
        const scope = eventScope(data);
        generatingStates.set(scope, {
          id: `generating-${scope}-${index}`,
          label: eventText(data, 'label') || 'Generating the next action...',
          tokenBuffer: '',
        });
        isGenerating = true;
        upsertGenerating(scope);
        break;
      }
      case 'token': {
        const scope = eventScope(data);
        const state = generatingStates.get(scope) || {
          id: `generating-${scope}-${index}`,
          label: 'Generating the next action...',
          tokenBuffer: '',
        };
        if (!generatingStates.has(scope)) {
          generatingStates.set(scope, state);
        }
        state.tokenBuffer += eventText(data, 'content', 'text');
        isGenerating = true;
        upsertGenerating(scope);
        break;
      }
      case 'step_start': {
        clearGenerating(eventScope(data));
        const stepNum = typeof data.step_num === 'number' ? data.step_num : items.length;
        const key = scopedKey(data, stepNum);
        const action = eventText(data, 'action') || 'step';
        const step: StepDisplay = {
          stepNum,
          action,
        };
        const item: TraceDisplayItem = { kind: 'step', id: `step-${key}`, data: step };
        stepIndex.set(key, items.length);
        items.push(item);
        break;
      }
      case 'step': {
        clearGenerating(eventScope(data));
        const stepNum = typeof data.step_num === 'number' ? data.step_num : items.length;
        const key = scopedKey(data, stepNum);
        const rawThought = eventText(data, 'thought');
        const next: StepDisplay = {
          stepNum,
          action: eventText(data, 'action') || 'step',
          thought: sanitizeThoughtText(rawThought),
          observation: eventText(data, 'observation') || undefined,
          verified: typeof data.verified === 'boolean' ? data.verified : null,
          reviewIssues: formatReviewIssues(data.reviews),
        };
        const idx = stepIndex.get(key);
        if (idx !== undefined && items[idx]?.kind === 'step') {
          items[idx] = { kind: 'step', id: `step-${key}`, data: next };
        } else {
          stepIndex.set(key, items.length);
          items.push({ kind: 'step', id: `step-${key}`, data: next });
        }
        break;
      }
      case 'tool_start': {
        clearGenerating(eventScope(data));
        const rawStep = data.step_num;
        const stepNum = typeof rawStep === 'number' ? rawStep : 0;
        // step_num may be the string "claim_check" for claim-phase calls;
        // key on the raw value so tool_done finds its tool_start.
        const stepKey = typeof rawStep === 'number' || typeof rawStep === 'string'
          ? String(rawStep)
          : `auto-${index}`;
        const key = `${eventScope(data)}-${stepKey}`;
        const tool = eventText(data, 'tool') || 'tool';
        const argsRaw = eventText(data, 'args_preview') || undefined;
        const argsPreview = argsRaw
          ? summarizeArgsPreview(argsRaw)
          : previewToolArgs(data.input) || undefined;
        const item: TraceDisplayItem = {
          kind: 'tool',
          id: `tool-${key}`,
          data: {
            stepNum,
            tool,
            running: true,
            argsPreview,
            argsRaw,
            startedAt: eventTimestamp(data),
          },
        };
        toolIndex.set(key, items.length);
        items.push(item);
        break;
      }
      case 'tool_done': {
        clearGenerating(eventScope(data));
        const rawStep = data.step_num;
        const stepNum = typeof rawStep === 'number' ? rawStep : 0;
        const stepKey = typeof rawStep === 'number' || typeof rawStep === 'string'
          ? String(rawStep)
          : `auto-${index}`;
        const key = `${eventScope(data)}-${stepKey}`;
        const tool = eventText(data, 'tool') || 'tool';
        const idx = toolIndex.get(key);
        const previous = idx !== undefined && items[idx]?.kind === 'tool'
          ? (items[idx] as { kind: 'tool'; data: ToolDisplay }).data
          : undefined;
        const doneAt = eventTimestamp(data);
        const durationMs = typeof data.duration_seconds === 'number'
          ? data.duration_seconds * 1000
          : previous?.startedAt !== undefined && doneAt !== undefined
            ? Math.max(0, doneAt - previous.startedAt)
            : undefined;
        const next: ToolDisplay = {
          stepNum,
          tool,
          running: false,
          output: eventText(data, 'output') || undefined,
          success: typeof data.success === 'boolean' ? data.success : undefined,
          argsPreview: previous?.argsPreview,
          argsRaw: previous?.argsRaw,
          startedAt: previous?.startedAt,
          durationMs,
        };
        if (idx !== undefined && items[idx]?.kind === 'tool') {
          items[idx] = { kind: 'tool', id: `tool-${key}`, data: next };
        } else {
          toolIndex.set(key, items.length);
          items.push({ kind: 'tool', id: `tool-${key}`, data: next });
        }
        break;
      }
      case 'done': {
        clearGenerating();
        hasAnswer = true;
        activeStage = null;
        items.push({ kind: 'event', id: `event-${index}`, event });
        break;
      }
      case 'tool_progress': {
        // Heartbeat/structured progress for a running tool: update its card in
        // place (elapsed timer, latest progress line) instead of adding rows.
        const rawStep = data.step_num;
        const stepKey = typeof rawStep === 'number' || typeof rawStep === 'string'
          ? String(rawStep)
          : `auto-${index}`;
        const key = `${eventScope(data)}-${stepKey}`;
        const idx = toolIndex.get(key);
        if (idx === undefined || items[idx]?.kind !== 'tool') break;
        const toolItem = items[idx] as Extract<TraceDisplayItem, { kind: 'tool' }>;
        if (!toolItem.data.running) break;
        const elapsed = typeof data.elapsed_seconds === 'number' ? data.elapsed_seconds : undefined;
        const detail = eventText(data, 'detail');
        items[idx] = {
          kind: 'tool',
          id: toolItem.id,
          data: {
            ...toolItem.data,
            elapsedSeconds: elapsed ?? toolItem.data.elapsedSeconds,
            progressMessage: detail || toolItem.data.progressMessage,
          },
        };
        break;
      }
      case 'session':
      case 'turn_started':
      case 'ping':
        break;
      case 'memory_retrieval':
      case 'user_memory_retrieval': {
        clearGenerating(eventScope(data));
        const last = items[items.length - 1];
        if (last?.kind === 'memory_retrieval') {
          last.count += 1;
        } else {
          items.push({
            kind: 'memory_retrieval',
            id: `memory-retrieval-${index}`,
            count: 1,
          });
        }
        break;
      }
      default:
        clearGenerating(eventScope(data));
        items.push({ kind: 'event', id: `event-${index}`, event });
        break;
    }
  });

  const answerItems = items.filter(
    (item) => item.kind === 'event' && item.event.type === 'done',
  );
  const processItems = items.filter(
    (item) => !(item.kind === 'event' && item.event.type === 'done'),
  );

  return {
    items,
    processItems,
    answerItems,
    activeStage,
    isGenerating,
    hasAnswer,
  };
}
