import { describe, it, expect } from 'vitest';
import { parseMathSegments, splitMarkdownBlocks } from '../components/MathText';

describe('parseMathSegments', () => {
  it('parses inline \\( ... \\) delimiters', () => {
    const segments = parseMathSegments('因为 \\(f_n\\to f\\) 依测度收敛');
    expect(segments).toHaveLength(3);
    expect(segments[0]).toMatchObject({ type: 'text', value: '因为 ' });
    expect(segments[1]).toMatchObject({ type: 'math', value: 'f_n\\to f', display: false });
    expect(segments[2]).toMatchObject({ type: 'text', value: ' 依测度收敛' });
  });

  it('parses block \\[ ... \\] delimiters', () => {
    const segments = parseMathSegments('因此\n\\[\n\\mu(E_k)<2^{-k}\n\\]\n成立');
    const math = segments.find((s) => s.type === 'math');
    expect(math).toMatchObject({ type: 'math', display: true });
    expect((math as { value: string }).value).toContain('\\mu(E_k)');
  });

  it('parses dollar delimiters', () => {
    const segments = parseMathSegments('inline $x^2$ and $$y^2$$');
    expect(segments.filter((s) => s.type === 'math')).toHaveLength(2);
  });

  it('repairs bell-escaped ellipsis and removes display control characters', () => {
    // Bell → \dots, then autoWrapUndelimitedMath wraps the LaTeX run in $...$.
    const segments = parseMathSegments('1,2,\u0007dots,2n\u0001');
    expect(segments).toEqual([
      { type: 'math', value: '1,2,\\dots,2n', display: false, raw: '$1,2,\\dots,2n$' },
    ]);
  });

  it('keeps bold wrappers that contain inline math', () => {
    const segments = parseMathSegments('**第一部分：\\(d \\le |L|-\\nu\\)**：继续');
    expect(segments).toHaveLength(2);
    expect(segments[0]).toMatchObject({ type: 'bold' });
    const bold = segments[0] as {
      type: 'bold';
      children: Array<{ type: string; value?: string }>;
    };
    expect(bold.children).toEqual([
      { type: 'text', value: '第一部分：' },
      { type: 'math', value: 'd \\le |L|-\\nu', display: false, raw: '\\(d \\le |L|-\\nu\\)' },
    ]);
    expect(segments[1]).toMatchObject({ type: 'text', value: '：继续' });
  });

  it('parses Hall-style theorem headings without leaking asterisks', () => {
    const segments = parseMathSegments(
      '**定理（Hall）**：设 \\(G=(L\\cup R, E)\\)。',
    );
    expect(segments.some((s) => s.type === 'text' && s.value.includes('**'))).toBe(false);
    expect(segments[0]).toMatchObject({ type: 'bold' });
    expect(segments.some((s) => s.type === 'math')).toBe(true);
  });
});

describe('splitMarkdownBlocks', () => {
  it('splits headings, rules, and paragraphs', () => {
    const blocks = splitMarkdownBlocks(
      '**结论：** 成立。\n\n---\n\n## 第一步：引入参数\n正文 \\(I(a)\\) 收敛。',
    );
    expect(blocks).toEqual([
      { type: 'paragraph', text: '**结论：** 成立。' },
      { type: 'hr' },
      { type: 'heading', level: 2, text: '第一步：引入参数' },
      { type: 'paragraph', text: '正文 \\(I(a)\\) 收敛。' },
    ]);
  });

  it('supports heading levels 1-4 and multiple rule styles', () => {
    const blocks = splitMarkdownBlocks('# 一\n***\n#### 四\n___\n文本');
    expect(blocks.map((b) => b.type)).toEqual([
      'heading',
      'hr',
      'heading',
      'hr',
      'paragraph',
    ]);
    expect(blocks[0]).toMatchObject({ level: 1 });
    expect(blocks[2]).toMatchObject({ level: 4 });
  });

  it('does not treat dashes inside text as a rule', () => {
    const blocks = splitMarkdownBlocks('范围是 1--2\n以及 a - b');
    expect(blocks).toEqual([{ type: 'paragraph', text: '范围是 1--2\n以及 a - b' }]);
  });

  it('parses a standalone markdown image line as an image block', () => {
    const blocks = splitMarkdownBlocks(
      '如下图：\n\n![函数图像](/api/solve/figures/sess-1/fig-1.png)\n\n如图所示。',
    );
    expect(blocks).toEqual([
      { type: 'paragraph', text: '如下图：' },
      {
        type: 'image',
        alt: '函数图像',
        url: '/api/solve/figures/sess-1/fig-1.png',
      },
      { type: 'paragraph', text: '如图所示。' },
    ]);
  });

  it('rejects image URLs outside /api/ and http(s)', () => {
    const blocks = splitMarkdownBlocks('![x](file:///etc/passwd)\n![y](javascript:alert(1))');
    expect(blocks).toEqual([
      { type: 'paragraph', text: '![x](file:///etc/passwd)\n![y](javascript:alert(1))' },
    ]);
  });
});
