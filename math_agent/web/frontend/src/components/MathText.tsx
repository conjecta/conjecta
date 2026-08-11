import { Fragment, useMemo, type ReactNode } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';

type MathSegment = { type: 'math'; value: string; display: boolean; raw: string };
type TextSegment = { type: 'text'; value: string };
type BoldSegment = { type: 'bold'; children: Array<TextSegment | MathSegment> };
type Segment = TextSegment | MathSegment | BoldSegment;

const MATH_DELIMITERS: { re: RegExp; display: boolean }[] = [
  { re: /\$\$([\s\S]+?)\$\$/g, display: true },
  { re: /\\\[([\s\S]+?)\\\]/g, display: true },
  { re: /\$([^$\n]+?)\$/g, display: false },
  { re: /\\\(([\s\S]+?)\\\)/g, display: false },
];

const PLACEHOLDER_RE = /\u0000MATH(\d+)\u0000/g;
const BOLD_RE = /\*\*((?:(?!\*\*)[\s\S])+?)\*\*/g;

function extractMath(text: string): { masked: string; maths: MathSegment[] } {
  const maths: MathSegment[] = [];
  let masked = text
    .replace(/\u0007dots/g, '\\dots')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '');

  // Replace earliest-match delimiters left-to-right so nested/overlapping
  // candidates stay consistent with the previous segmenter.
  let pos = 0;
  let out = '';
  while (pos < masked.length) {
    let earliest: { display: boolean; match: RegExpExecArray } | null = null;
    for (const delimiter of MATH_DELIMITERS) {
      delimiter.re.lastIndex = pos;
      const match = delimiter.re.exec(masked);
      if (match && (earliest === null || match.index < earliest.match.index)) {
        earliest = { display: delimiter.display, match };
      }
    }
    if (!earliest) {
      out += masked.slice(pos);
      break;
    }
    out += masked.slice(pos, earliest.match.index);
    const index = maths.length;
    maths.push({
      type: 'math',
      value: earliest.match[1],
      display: earliest.display,
      raw: earliest.match[0],
    });
    out += `\u0000MATH${index}\u0000`;
    pos = earliest.match.index + earliest.match[0].length;
  }
  return { masked: out, maths };
}

function expandPlaceholders(text: string, maths: MathSegment[]): Array<TextSegment | MathSegment> {
  const parts: Array<TextSegment | MathSegment> = [];
  let last = 0;
  PLACEHOLDER_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = PLACEHOLDER_RE.exec(text)) !== null) {
    if (match.index > last) {
      parts.push({ type: 'text', value: text.slice(last, match.index) });
    }
    const math = maths[Number(match[1])];
    if (math) parts.push(math);
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    parts.push({ type: 'text', value: text.slice(last) });
  }
  return parts;
}

const MATH_RUN_RE =
  /[A-Za-z0-9\\{}()\[\]_^=+<>|*,.'-]+(?: [A-Za-z0-9\\{}()\[\]_^=+<>|*,.'-]+)*/g;
const PURE_WORD_RE = /^[A-Za-z]{2,}$/;
const NUL = String.fromCharCode(0);

function looksMathy(run: string): boolean {
  if (run.includes('\\')) return true;
  if (/[A-Za-z]/.test(run) && /[A-Za-z0-9}]\s*[=<>]\s*/.test(run)) return true;
  if (/[_^]\{?[A-Za-z0-9]/.test(run)) return true;
  return false;
}

/**
 * Models sometimes emit bare LaTeX (e.g. `S_n=\sum_{k=1}^n 1/k^2`) with no
 * delimiters. Wrap math-looking runs in $...$ so they render; CJK text and
 * punctuation act as natural run boundaries.
 */
export function autoWrapUndelimitedMath(text: string): string {
  return text.replace(MATH_RUN_RE, (run) => {
    if (!looksMathy(run)) return run;
    // Trim plain prose words glued to the run's edges ("in \mathbb R" etc.).
    let pieces = run.split(' ');
    while (pieces.length > 1 && PURE_WORD_RE.test(pieces[0])) pieces = pieces.slice(1);
    while (pieces.length > 1 && PURE_WORD_RE.test(pieces[pieces.length - 1])) {
      pieces = pieces.slice(0, -1);
    }
    const core = pieces.join(' ');
    if (!core || !looksMathy(core)) return run;
    const start = run.indexOf(core);
    return `${run.slice(0, start)}$${core}$${run.slice(start + core.length)}`;
  });
}

/** Extract the $...$ segments added by autoWrapUndelimitedMath (inline only). */
function extractAutoWrappedDollars(masked: string, maths: MathSegment[]): string {
  let out = '';
  let i = 0;
  while (i < masked.length) {
    if (masked[i] === '$') {
      const end = masked.indexOf('$', i + 1);
      if (end > i + 1) {
        const value = masked.slice(i + 1, end);
        if (!value.includes('\n')) {
          const index = maths.length;
          maths.push({ type: 'math', value, display: false, raw: masked.slice(i, end + 1) });
          out += `${NUL}MATH${index}${NUL}`;
          i = end + 1;
          continue;
        }
      }
    }
    out += masked[i];
    i += 1;
  }
  return out;
}

/** Parse math first (placeholders), then **bold** so bold may wrap formulas. */
export function parseMathSegments(text: string): Segment[] {
  if (!text) return [];
  const first = extractMath(text);
  const masked = extractAutoWrappedDollars(
    autoWrapUndelimitedMath(first.masked),
    first.maths,
  );
  const { maths } = first;
  const segments: Segment[] = [];
  let last = 0;
  BOLD_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = BOLD_RE.exec(masked)) !== null) {
    if (match.index > last) {
      segments.push(...expandPlaceholders(masked.slice(last, match.index), maths));
    }
    segments.push({
      type: 'bold',
      children: expandPlaceholders(match[1], maths),
    });
    last = match.index + match[0].length;
  }
  if (last < masked.length) {
    segments.push(...expandPlaceholders(masked.slice(last), maths));
  }
  return segments;
}

type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'hr' }
  | { type: 'image'; alt: string; url: string }
  | { type: 'paragraph'; text: string };

const HEADING_RE = /^(#{1,4})\s+(.*)$/;
const HR_RE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
// Only same-origin API URLs (tool-generated figures) or plain http(s) images
// render as <img>; anything else stays plain text.
const IMAGE_RE = /^!\[([^\]]*)\]\((\/api\/[^\s)]+|https?:\/\/[^\s)]+)\)$/;

/** Split raw text into heading / horizontal-rule / image / paragraph blocks (line-based). */
export function splitMarkdownBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    const value = paragraph.join('\n').replace(/^\n+|\n+$/g, '');
    paragraph = [];
    if (value.trim()) blocks.push({ type: 'paragraph', text: value });
  };
  for (const line of text.split('\n')) {
    const image = IMAGE_RE.exec(line.trim());
    if (image) {
      flush();
      blocks.push({ type: 'image', alt: image[1], url: image[2] });
      continue;
    }
    const heading = HEADING_RE.exec(line);
    if (heading) {
      flush();
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] });
      continue;
    }
    if (HR_RE.test(line)) {
      flush();
      blocks.push({ type: 'hr' });
      continue;
    }
    paragraph.push(line);
  }
  flush();
  return blocks;
}

function renderMathSegment(segment: MathSegment): string {
  try {
    return katex.renderToString(segment.value.trim(), {
      displayMode: segment.display,
      throwOnError: false,
    });
  } catch {
    return segment.raw;
  }
}

function renderInline(seg: TextSegment | MathSegment, key: string): ReactNode {
  if (seg.type === 'text') {
    return <span key={key}>{seg.value}</span>;
  }
  const html = renderMathSegment(seg);
  if (seg.display) {
    return (
      <span
        key={key}
        className="my-4 block overflow-x-auto text-[1.06em]"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }
  return <span key={key} dangerouslySetInnerHTML={{ __html: html }} />;
}

const HEADING_CLASSES = [
  'mb-1 mt-4 block text-base font-bold',
  'mb-1 mt-3 block text-[15px] font-semibold',
  'mb-0.5 mt-2 block text-sm font-semibold',
  'mb-0.5 mt-2 block text-sm font-medium',
];

function InlineSegments({ text }: { text: string }) {
  const segments = useMemo(() => parseMathSegments(text), [text]);
  return (
    <>
      {segments.map((seg, i) => {
        if (seg.type === 'bold') {
          return (
            <strong key={i} className="font-semibold">
              {seg.children.map((child, j) => renderInline(child, `${i}-${j}`))}
            </strong>
          );
        }
        return <Fragment key={i}>{renderInline(seg, String(i))}</Fragment>;
      })}
    </>
  );
}

export function MathText({ text }: { text: string }) {
  const blocks = useMemo(() => splitMarkdownBlocks(text), [text]);

  return (
    <span className="math-text whitespace-pre-wrap leading-relaxed">
      {blocks.map((block, i) => {
        if (block.type === 'hr') {
          return <span key={i} className="my-3 block border-t border-border/70" />;
        }
        if (block.type === 'image') {
          return (
            <span key={i} className="my-2 block">
              <img
                src={block.url}
                alt={block.alt}
                className="max-w-full rounded-md border border-border/60"
              />
              {block.alt && (
                <span className="mt-1 block text-xs text-muted-foreground">{block.alt}</span>
              )}
            </span>
          );
        }
        if (block.type === 'heading') {
          return (
            <span key={i} className={HEADING_CLASSES[block.level - 1]}>
              <InlineSegments text={block.text} />
            </span>
          );
        }
        return <InlineSegments key={i} text={block.text} />;
      })}
    </span>
  );
}
