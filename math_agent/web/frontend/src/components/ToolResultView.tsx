import {
  extractComputeCode,
  isSearchTool,
  parseSearchHits,
  TOOL_CODE_MAX,
  TOOL_OUTPUT_MAX,
  truncateText,
} from '@/lib/toolRender';

export interface ToolResultBodyProps {
  tool: string;
  /** One-line args summary (already truncated for headers). */
  argsPreview?: string;
  /** Raw args_preview payload from the event (full JSON for compute). */
  argsRaw?: string;
  output?: string;
  success?: boolean;
  running?: boolean;
}

function SectionLabel({ children }: { children: string }) {
  return (
    <div className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">{children}</div>
  );
}

function TruncationNote() {
  return <div className="mt-1 text-[10px] text-muted-foreground">…（已截断）</div>;
}

const TERMINAL_CLASS =
  'overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-zinc-950 px-3 py-2 font-mono text-[11px] leading-relaxed text-zinc-200';

/** compute: 「代码」块（terminal 风格，取 args_preview JSON 的 code 字段）
 * + 「输出」块（成功绿色 / 失败红色左边框）。 */
function ComputeToolBody({ argsRaw, argsPreview, output, success, running }: ToolResultBodyProps) {
  const code = extractComputeCode(argsRaw) ?? argsRaw ?? argsPreview ?? '';
  const codeClamped = truncateText(code, TOOL_CODE_MAX);
  const outputClamped = truncateText(output, TOOL_OUTPUT_MAX);
  const borderClass = success === false ? 'border-l-destructive' : 'border-l-success';

  return (
    <div className="space-y-2">
      {codeClamped.text ? (
        <div>
          <SectionLabel>代码</SectionLabel>
          <pre className={TERMINAL_CLASS}>{codeClamped.text}</pre>
          {codeClamped.truncated ? <TruncationNote /> : null}
        </div>
      ) : null}
      <div>
        <SectionLabel>输出</SectionLabel>
        {outputClamped.text ? (
          <>
            <pre className={`${TERMINAL_CLASS} border-l-2 ${borderClass}`}>{outputClamped.text}</pre>
            {outputClamped.truncated ? <TruncationNote /> : null}
          </>
        ) : (
          <div className="text-[11px] text-muted-foreground">{running ? '运行中…' : '（无输出）'}</div>
        )}
      </div>
    </div>
  );
}

/** 搜索类工具：命中列表（标题 + 摘要），解析失败回退纯文本输出。 */
function SearchToolBody({ output, running }: ToolResultBodyProps) {
  const hits = parseSearchHits(output);
  if (hits) {
    return (
      <div>
        <SectionLabel>{`命中 ${hits.length} 条`}</SectionLabel>
        <ul className="space-y-1.5">
          {hits.map((hit, index) => (
            <li key={index} className="rounded-md border bg-card px-2.5 py-1.5">
              <div className="text-[11px] font-medium leading-snug text-foreground">{hit.title}</div>
              {hit.snippet ? (
                <div className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{hit.snippet}</div>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  return <PlainOutput output={output} running={running} />;
}

function PlainOutput({ output, running }: { output?: string; running?: boolean }) {
  const clamped = truncateText(output, TOOL_OUTPUT_MAX);
  return (
    <div>
      <SectionLabel>输出</SectionLabel>
      {clamped.text ? (
        <>
          <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-muted-foreground">
            {clamped.text}
          </pre>
          {clamped.truncated ? <TruncationNote /> : null}
        </>
      ) : (
        <div className="text-[11px] text-muted-foreground">{running ? '运行中…' : '（无输出）'}</div>
      )}
    </div>
  );
}

/** Expanded body of a tool card, dispatched by tool kind. */
export function ToolResultBody(props: ToolResultBodyProps) {
  if (props.tool === 'compute') return <ComputeToolBody {...props} />;
  if (isSearchTool(props.tool)) return <SearchToolBody {...props} />;
  return (
    <div className="space-y-1.5">
      {props.argsPreview ? (
        <div>
          <SectionLabel>参数</SectionLabel>
          <div className="break-words font-mono text-[11px] text-muted-foreground">{props.argsPreview}</div>
        </div>
      ) : null}
      <PlainOutput output={props.output} running={props.running} />
    </div>
  );
}
