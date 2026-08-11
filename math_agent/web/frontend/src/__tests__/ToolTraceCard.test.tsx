// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ToolTraceCard } from '@/components/TraceItem';
import type { ToolDisplay } from '@/lib/traceDisplay';

function renderCard(data: Partial<ToolDisplay>) {
  const full: ToolDisplay = { stepNum: 1, tool: 'compute', running: false, ...data };
  return render(<ToolTraceCard data={full} />);
}

function expand() {
  fireEvent.click(screen.getByRole('button'));
}

describe('ToolTraceCard rich rendering', () => {
  afterEach(() => {
    cleanup();
  });

  it('stays collapsed by default and shows the one-line args summary', () => {
    renderCard({ tool: 'compute', argsPreview: 'code: print(1)', argsRaw: '{"code":"print(1)"}' });
    expect(screen.getByText('code: print(1)')).toBeInTheDocument();
    expect(screen.queryByText('输出')).not.toBeInTheDocument();
  });

  it('renders compute code and output blocks when expanded', () => {
    renderCard({
      tool: 'compute',
      argsRaw: JSON.stringify({ code: 'def f(x):\n    return x * 2' }),
      argsPreview: 'code: def f(x): …',
      output: '21',
      success: true,
    });
    expand();
    expect(screen.getByText('代码')).toBeInTheDocument();
    // The collapsed header keeps the one-line summary; the expanded 代码
    // block shows the full multi-line code, so the text appears twice.
    expect(screen.getAllByText(/def f\(x\):/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('输出')).toBeInTheDocument();
    expect(screen.getByText('21')).toBeInTheDocument();
  });

  it('falls back to the raw args when the compute args JSON has no code field', () => {
    renderCard({
      tool: 'compute',
      argsRaw: '{"unexpected": true}',
      output: 'ok',
      success: true,
    });
    expand();
    expect(screen.getByText('{"unexpected": true}')).toBeInTheDocument();
  });

  it('renders search hits as a title + snippet list', () => {
    renderCard({
      tool: 'search_knowledge',
      output: JSON.stringify({
        results: [
          { title: 'Catalan 数', snippet: '组合计数中的经典序列' },
          { statement: 'Hall 婚配定理', body: '二部图匹配条件' },
        ],
      }),
      success: true,
    });
    expand();
    expect(screen.getByText('Catalan 数')).toBeInTheDocument();
    expect(screen.getByText('组合计数中的经典序列')).toBeInTheDocument();
    expect(screen.getByText('Hall 婚配定理')).toBeInTheDocument();
  });

  it('falls back to plain text when search output is not a hit list', () => {
    renderCard({ tool: 'mcp_arxiv_search', output: 'Found 2 papers: ...', success: true });
    expand();
    expect(screen.getByText('Found 2 papers: ...')).toBeInTheDocument();
  });

  it('marks outputs longer than 2000 chars as truncated', () => {
    renderCard({ tool: 'compute', output: 'y'.repeat(2500), success: true });
    expand();
    expect(screen.getByText('…（已截断）')).toBeInTheDocument();
  });

  it('keeps the plain 参数 + 输出 layout for other tools', () => {
    renderCard({
      tool: 'lean_check',
      argsPreview: 'code: trivial',
      output: 'no goals',
      success: true,
    });
    expand();
    expect(screen.getByText('参数')).toBeInTheDocument();
    // Header summary + expanded 参数 block both carry the one-line args.
    expect(screen.getAllByText('code: trivial')).toHaveLength(2);
    expect(screen.getByText('no goals')).toBeInTheDocument();
  });
});
