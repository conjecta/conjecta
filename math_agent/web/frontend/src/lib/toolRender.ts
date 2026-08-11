/** Pure helpers for rich per-tool rendering of tool call results. */

export const TOOL_CODE_MAX = 2000;
export const TOOL_OUTPUT_MAX = 2000;
export const SEARCH_SNIPPET_MAX = 200;

export interface TruncatedText {
  text: string;
  truncated: boolean;
}

/** Clamp display text to `max` chars; callers append 「…（已截断）」 when truncated. */
export function truncateText(raw: string | undefined, max: number): TruncatedText {
  const text = raw ?? '';
  if (text.length <= max) return { text, truncated: false };
  return { text: text.slice(0, max), truncated: true };
}

/** Pull the `code` field out of a compute tool's args_preview JSON.
 * Returns null when the payload is not JSON or has no string code. */
export function extractComputeCode(argsRaw: string | undefined): string | null {
  if (!argsRaw) return null;
  try {
    const parsed: unknown = JSON.parse(argsRaw);
    if (parsed && typeof parsed === 'object') {
      const code = (parsed as Record<string, unknown>).code;
      if (typeof code === 'string' && code.trim()) return code;
    }
  } catch {
    /* fall through */
  }
  return null;
}

/** Search-like tools: search_knowledge, search_mathlib, mcp_arxiv_*, web_search… */
export function isSearchTool(tool: string): boolean {
  return /search|arxiv|knowledge/i.test(tool);
}

export interface SearchHit {
  title: string;
  snippet?: string;
}

const TITLE_KEYS = ['title', 'statement', 'name'];
const SNIPPET_KEYS = ['body', 'why', 'snippet', 'output'];

function firstString(data: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

/** Try to parse a search tool's output into a hit list.
 * Accepts a JSON array, or an object with a `results`/`items` array.
 * Returns null when the output is not a recognizable hit list (caller
 * falls back to plain text). */
export function parseSearchHits(output: string | undefined): SearchHit[] | null {
  if (!output) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(output);
  } catch {
    return null;
  }
  let list: unknown[] | null = null;
  if (Array.isArray(parsed)) {
    list = parsed;
  } else if (parsed && typeof parsed === 'object') {
    const data = parsed as Record<string, unknown>;
    if (Array.isArray(data.results)) list = data.results;
    else if (Array.isArray(data.items)) list = data.items;
  }
  if (!list) return null;

  const hits: SearchHit[] = [];
  for (const entry of list) {
    if (typeof entry === 'string' && entry.trim()) {
      hits.push({ title: truncateText(entry.trim(), SEARCH_SNIPPET_MAX).text });
      continue;
    }
    if (!entry || typeof entry !== 'object') continue;
    const data = entry as Record<string, unknown>;
    const title = firstString(data, TITLE_KEYS);
    if (!title) continue;
    const snippet = firstString(data, SNIPPET_KEYS);
    hits.push({
      title,
      snippet: snippet ? truncateText(snippet, SEARCH_SNIPPET_MAX).text : undefined,
    });
  }
  return hits.length > 0 ? hits : null;
}

/** One-line summary of a raw args_preview JSON string, for collapsed headers. */
export function summarizeArgsPreview(raw: string, max = 140): string {
  const flat = (text: string) => {
    const oneLine = text.replace(/\s+/g, ' ').trim();
    return oneLine.length > max ? `${oneLine.slice(0, max - 1)}…` : oneLine;
  };
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const parts: string[] = [];
      for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
        if (typeof value === 'string') parts.push(`${key}: ${value}`);
        else {
          try {
            parts.push(`${key}: ${JSON.stringify(value)}`);
          } catch {
            parts.push(`${key}: ${String(value)}`);
          }
        }
      }
      if (parts.length > 0) return flat(parts.join(' · '));
    }
  } catch {
    /* not JSON — fall through */
  }
  return flat(raw);
}
