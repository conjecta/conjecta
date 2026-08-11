import { describe, expect, it } from 'vitest';
import {
  extractComputeCode,
  isSearchTool,
  parseSearchHits,
  summarizeArgsPreview,
  truncateText,
} from '@/lib/toolRender';

describe('truncateText', () => {
  it('keeps short text untouched', () => {
    expect(truncateText('abc', 10)).toEqual({ text: 'abc', truncated: false });
  });

  it('clamps long text and flags truncation', () => {
    const result = truncateText('x'.repeat(2500), 2000);
    expect(result.text).toHaveLength(2000);
    expect(result.truncated).toBe(true);
  });

  it('handles undefined input', () => {
    expect(truncateText(undefined, 10)).toEqual({ text: '', truncated: false });
  });
});

describe('extractComputeCode', () => {
  it('extracts the code field from args_preview JSON', () => {
    const raw = JSON.stringify({ code: 'print(2 + 2)' });
    expect(extractComputeCode(raw)).toBe('print(2 + 2)');
  });

  it('preserves multi-line code', () => {
    const raw = JSON.stringify({ code: 'def f(x):\n    return x * 2\n\nprint(f(21))' });
    expect(extractComputeCode(raw)).toBe('def f(x):\n    return x * 2\n\nprint(f(21))');
  });

  it('returns null for non-JSON input', () => {
    expect(extractComputeCode('not json at all')).toBeNull();
  });

  it('returns null when the JSON has no string code field', () => {
    expect(extractComputeCode(JSON.stringify({ query: 'Catalan' }))).toBeNull();
    expect(extractComputeCode(JSON.stringify({ code: 42 }))).toBeNull();
    expect(extractComputeCode(undefined)).toBeNull();
  });
});

describe('isSearchTool', () => {
  it('matches search / arxiv / knowledge tool names', () => {
    expect(isSearchTool('search_knowledge')).toBe(true);
    expect(isSearchTool('search_mathlib')).toBe(true);
    expect(isSearchTool('mcp_arxiv_search')).toBe(true);
    expect(isSearchTool('web_search')).toBe(true);
    expect(isSearchTool('knowledge_lookup')).toBe(true);
  });

  it('does not match unrelated tools', () => {
    expect(isSearchTool('compute')).toBe(false);
    expect(isSearchTool('lean_check')).toBe(false);
  });
});

describe('parseSearchHits', () => {
  it('parses a bare JSON array of hit objects', () => {
    const output = JSON.stringify([
      { title: 'Catalan 数', snippet: '组合计数中的经典序列' },
      { statement: 'Hall 婚配定理', body: '二部图匹配的充要条件' },
    ]);
    const hits = parseSearchHits(output);
    expect(hits).toEqual([
      { title: 'Catalan 数', snippet: '组合计数中的经典序列' },
      { title: 'Hall 婚配定理', snippet: '二部图匹配的充要条件' },
    ]);
  });

  it('parses an object with results or items', () => {
    const withResults = parseSearchHits(JSON.stringify({ results: [{ name: '条目 A' }] }));
    expect(withResults).toEqual([{ title: '条目 A', snippet: undefined }]);

    const withItems = parseSearchHits(JSON.stringify({ items: [{ title: '条目 B', why: '相关' }] }));
    expect(withItems).toEqual([{ title: '条目 B', snippet: '相关' }]);
  });

  it('prefers the first available title field', () => {
    const hits = parseSearchHits(JSON.stringify([{ title: 'T', statement: 'S', name: 'N' }]));
    expect(hits?.[0].title).toBe('T');
    const fallback = parseSearchHits(JSON.stringify([{ name: 'N', statement: 'S' }]));
    expect(fallback?.[0].title).toBe('S');
  });

  it('truncates snippets to 200 chars', () => {
    const hits = parseSearchHits(JSON.stringify([{ title: 'T', body: 'y'.repeat(300) }]));
    expect(hits?.[0].snippet).toHaveLength(200);
  });

  it('accepts plain-string entries', () => {
    const hits = parseSearchHits(JSON.stringify(['一条纯文本结果']));
    expect(hits).toEqual([{ title: '一条纯文本结果' }]);
  });

  it('returns null for non-JSON output so callers fall back to plain text', () => {
    expect(parseSearchHits('Found 3 papers: 1) ...')).toBeNull();
    expect(parseSearchHits(undefined)).toBeNull();
  });

  it('returns null when no entry has a usable title', () => {
    expect(parseSearchHits(JSON.stringify([{ foo: 1 }, 42]))).toBeNull();
    expect(parseSearchHits(JSON.stringify([]))).toBeNull();
  });
});

describe('summarizeArgsPreview', () => {
  it('summarizes JSON args into a one-line preview', () => {
    const raw = JSON.stringify({ code: 'print(1)' });
    expect(summarizeArgsPreview(raw)).toBe('code: print(1)');
  });

  it('truncates long values to one line', () => {
    const raw = JSON.stringify({ code: `print(${'x'.repeat(300)})` });
    const summary = summarizeArgsPreview(raw);
    expect(summary.length).toBeLessThanOrEqual(140);
    expect(summary.endsWith('…')).toBe(true);
    expect(summary).not.toContain('\n');
  });

  it('flattens non-JSON text', () => {
    expect(summarizeArgsPreview('line one\nline two')).toBe('line one line two');
  });
});
