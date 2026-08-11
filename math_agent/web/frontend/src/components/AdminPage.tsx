import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Database,
  RefreshCw,
  Search,
  Users,
  Zap,
} from 'lucide-react';
import { apiFetch } from '@/api/client';
import { fetchAdminFeedback } from '@/api/feedback';
import { AnswerCard } from '@/components/AnswerCard';
import { useAuthStore } from '@/store/auth';
import type { AdminOverview, AdminRunRecord } from '@/types/admin';
import type { FeedbackRating } from '@/types/feedback';

const number = new Intl.NumberFormat('zh-CN');

function compactNumber(value: number): string {
  if (value < 1000) return number.format(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value >= 100_000 ? 0 : 1)}K`;
  return `${(value / 1_000_000).toFixed(value >= 100_000_000 ? 0 : 1)}M`;
}

function dateTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

function duration(ms: number): string {
  if (!ms) return '—';
  if (ms < 60_000) return `${Math.round(ms / 1000)} 秒`;
  return `${Math.floor(ms / 60_000)}分 ${Math.round((ms % 60_000) / 1000)}秒`;
}

const STATUS_META: Record<string, { label: string; className: string }> = {
  completed: { label: '已完成', className: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' },
  failed: { label: '失败', className: 'bg-red-500/10 text-red-700 dark:text-red-300' },
  running: { label: '运行中', className: 'bg-blue-500/10 text-blue-700 dark:text-blue-300' },
  waiting: { label: '待确认', className: 'bg-amber-500/10 text-amber-700 dark:text-amber-300' },
  cancelled: { label: '已停止', className: 'bg-muted text-muted-foreground' },
};

function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] || { label: status || '未知', className: 'bg-muted text-muted-foreground' };
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${meta.className}`}>
      {meta.label}
    </span>
  );
}

const RATING_META: Record<FeedbackRating, { label: string; className: string }> = {
  satisfied: { label: '满意', className: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' },
  unsatisfied: { label: '不满意', className: 'bg-red-500/10 text-red-700 dark:text-red-300' },
};

function RatingBadge({ rating }: { rating: FeedbackRating }) {
  const meta = RATING_META[rating];
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${meta.className}`}>
      {meta.label}
    </span>
  );
}

function MetricCard({
  label,
  value,
  note,
  icon: Icon,
}: {
  label: string;
  value: string;
  note: string;
  icon: typeof Activity;
}) {
  return (
    <article className="admin-metric">
      <div className="flex items-center justify-between text-muted-foreground">
        <span className="text-xs font-semibold">{label}</span>
        <Icon size={15} />
      </div>
      <p className="mt-5 font-mono text-2xl font-semibold tracking-[-0.04em] text-foreground">{value}</p>
      <p className="mt-1 text-[11px] text-muted-foreground">{note}</p>
    </article>
  );
}

function TokenTrend({ data }: { data: AdminOverview['daily'] }) {
  const max = Math.max(...data.map((item) => item.tokens), 1);
  const total = data.reduce((sum, item) => sum + item.tokens, 0);
  return (
    <section className="admin-surface min-w-0 p-5 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="admin-eyebrow">TOKEN PULSE</p>
          <h2 className="mt-2 text-lg font-bold tracking-tight">每日消耗趋势</h2>
        </div>
        <div className="text-right">
          <p className="font-mono text-lg font-semibold">{compactNumber(total)}</p>
          <p className="text-[11px] text-muted-foreground">当前周期总 token</p>
        </div>
      </div>
      <div className="mt-7 flex h-44 items-end gap-1.5" aria-label="每日 token 消耗柱状图">
        {data.map((item, index) => {
          const height = item.tokens ? Math.max(5, (item.tokens / max) * 100) : 2;
          const showLabel = data.length <= 14 || index % 5 === 0 || index === data.length - 1;
          return (
            <div key={item.date} className="group flex min-w-0 flex-1 flex-col items-center justify-end gap-2">
              <div className="relative flex h-32 w-full items-end justify-center">
                <div
                  className="w-full max-w-7 rounded-t-sm bg-primary/75 transition-all duration-300 group-hover:bg-primary"
                  style={{ height: `${height}%` }}
                />
                <div className="pointer-events-none absolute bottom-[calc(100%+8px)] z-20 hidden w-max rounded-lg bg-foreground px-2.5 py-1.5 text-[10px] text-background shadow-lg group-hover:block">
                  {item.date.slice(5)} · {number.format(item.tokens)} token · {item.runs} 次
                </div>
              </div>
              <span className="hidden h-4 w-full overflow-hidden text-center font-mono text-[9px] text-muted-foreground sm:block">
                {showLabel ? item.date.slice(5).replace('-', '/') : ''}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function RunDetails({ record }: { record: AdminRunRecord }) {
  return (
    <details className="group">
      <summary className="grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_88px_68px_18px] items-center gap-2.5 border-t px-4 py-3.5 text-sm transition-colors hover:bg-accent/35 sm:gap-4 sm:px-5 lg:grid-cols-[minmax(0,1fr)_120px_120px_110px_100px_84px_32px]">
        <div className="min-w-0">
          <p className="truncate font-medium">{record.problem || '未记录题目'}</p>
          <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">{record.id}</p>
        </div>
        <span className="font-mono text-xs">{record.phone}</span>
        <span className="hidden truncate text-xs text-muted-foreground lg:block">{record.model.split('/').pop()}</span>
        <span className="hidden font-mono text-xs lg:block">{compactNumber(record.total_tokens)}</span>
        <span className="hidden text-xs text-muted-foreground lg:block">{duration(record.duration_ms)}</span>
        <StatusBadge status={record.status} />
        <ChevronDown size={15} className="text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="border-t bg-muted/25 px-5 py-4">
        <div className="max-w-4xl">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">题目</p>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{record.problem}</p>
          {record.answer ? (
            <AnswerCard text={record.answer} title="完整回答" className="mt-4" />
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">暂无完整回答（可能仍在运行，或历史记录未落库）。</p>
          )}
        </div>
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 font-mono text-[11px] text-muted-foreground">
          <span>输入 {number.format(record.input_tokens)}</span>
          <span>输出 {number.format(record.output_tokens)}</span>
          <span>缓存 {number.format(record.cached_tokens)}</span>
          <span>推理 {number.format(record.reasoning_tokens)}</span>
          <span>模式 {record.mode}</span>
          <span>开始 {dateTime(record.started_at)}</span>
        </div>
      </div>
    </details>
  );
}

export function AdminPage() {
  const user = useAuthStore((state) => state.user);
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState<'records' | 'users' | 'feedback'>('records');
  const [query, setQuery] = useState('');
  const [rating, setRating] = useState<'all' | FeedbackRating>('all');
  const overview = useQuery({
    queryKey: ['admin-overview', days],
    queryFn: () => apiFetch<AdminOverview>(`/api/admin/overview?days=${days}&limit=200`),
    enabled: Boolean(user?.is_admin),
    refetchInterval: 60_000,
  });
  const feedback = useQuery({
    queryKey: ['admin-feedback'],
    queryFn: () => fetchAdminFeedback({ limit: 200 }),
    enabled: Boolean(user?.is_admin),
    refetchInterval: 60_000,
  });

  const filteredRecords = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return overview.data?.records || [];
    return (overview.data?.records || []).filter((item) =>
      [item.problem, item.phone, item.model, item.id, item.status].some((value) =>
        String(value || '').toLowerCase().includes(needle),
      ),
    );
  }, [overview.data?.records, query]);

  const filteredUsers = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return overview.data?.users || [];
    return (overview.data?.users || []).filter((item) =>
      `${item.phone} ${item.id}`.toLowerCase().includes(needle),
    );
  }, [overview.data?.users, query]);

  const filteredFeedback = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (feedback.data?.feedback || []).filter((item) => {
      const matchesRating = rating === 'all' || item.rating === rating;
      const matchesQuery =
        !needle ||
        [item.label, item.problem_preview, item.comment].some((value) =>
          String(value || '').toLowerCase().includes(needle),
        );
      return matchesRating && matchesQuery;
    });
  }, [feedback.data?.feedback, query, rating]);

  if (!user?.is_admin) {
    return (
      <main className="grid min-h-[100dvh] place-items-center px-6">
        <div className="max-w-md text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-destructive/10 text-destructive">
            <CircleAlert size={22} />
          </div>
          <h1 className="mt-5 text-xl font-bold">无法访问运营中心</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">当前账号没有管理员权限。</p>
          <a href="/app" className="mt-6 inline-flex items-center gap-2 rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background">
            <ArrowLeft size={15} /> 返回工作台
          </a>
        </div>
      </main>
    );
  }

  const data = overview.data;
  return (
    <div className="min-h-[100dvh] bg-background">
      <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1480px] items-center justify-between px-4 sm:px-7">
          <div className="flex items-center gap-3">
            <a href="/app" aria-label="返回工作台" className="admin-icon-button">
              <ArrowLeft size={17} />
            </a>
            <div>
              <p className="text-sm font-bold tracking-[-0.025em]"><span className="hidden sm:inline">Conjecta / </span>运营中心</p>
              <p className="hidden font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground sm:block">System ledger</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-lg border bg-card p-0.5">
              {[7, 30, 90].map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setDays(value)}
                  className={`rounded-md px-2.5 py-1.5 font-mono text-[11px] transition-colors ${days === value ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'}`}
                >
                  {value}天
                </button>
              ))}
            </div>
            <button
              type="button"
              aria-label="刷新数据"
              onClick={() => {
                overview.refetch();
                feedback.refetch();
              }}
              className="admin-icon-button"
              disabled={overview.isFetching || feedback.isFetching}
            >
              <RefreshCw size={16} className={overview.isFetching || feedback.isFetching ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1480px] px-4 py-7 sm:px-7 sm:py-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="admin-eyebrow">OPERATIONS / {days} DAYS</p>
            <h1 className="mt-2 text-2xl font-bold tracking-[-0.035em] sm:text-3xl">用户与用量总览</h1>
            <p className="mt-2 text-sm text-muted-foreground">从用户活跃一直追踪到单次模型调用。</p>
          </div>
          {data && <p className="font-mono text-[10px] text-muted-foreground">更新于 {dateTime(data.generated_at)}</p>}
        </div>

        {overview.isLoading && (
          <div className="mt-12 flex items-center justify-center gap-3 py-24 text-sm text-muted-foreground">
            <RefreshCw size={17} className="animate-spin" /> 正在汇总运营数据
          </div>
        )}
        {overview.isError && (
          <div className="admin-surface mt-8 flex items-start gap-3 border-destructive/30 p-5 text-sm">
            <CircleAlert className="mt-0.5 shrink-0 text-destructive" size={18} />
            <div>
              <p className="font-semibold">暂时无法读取运营数据</p>
              <p className="mt-1 text-muted-foreground">请确认已执行运营数据表迁移，并配置 Supabase service role。</p>
            </div>
          </div>
        )}

        {data && (
          <>
            <section className="mt-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <MetricCard label="总 token" value={compactNumber(data.summary.total_tokens)} note={`输入 ${compactNumber(data.summary.input_tokens)} / 输出 ${compactNumber(data.summary.output_tokens)}`} icon={Zap} />
              <MetricCard label="活跃用户" value={number.format(data.summary.active_users)} note={`注册用户 ${number.format(data.summary.users)}`} icon={Users} />
              <MetricCard label="解题次数" value={number.format(data.summary.runs)} note={`平均 ${compactNumber(data.summary.avg_tokens_per_run)} token / 次`} icon={Activity} />
              <MetricCard label="完成率" value={`${data.summary.runs ? Math.round((data.summary.completed_runs / data.summary.runs) * 100) : 0}%`} note={`${data.summary.completed_runs} 完成 / ${data.summary.failed_runs} 失败`} icon={CheckCircle2} />
              <MetricCard label="缓存命中 token" value={compactNumber(data.summary.cached_tokens)} note={`推理 token ${compactNumber(data.summary.reasoning_tokens)}`} icon={Database} />
            </section>

            <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(290px,.45fr)]">
              <TokenTrend data={data.daily} />
              <section className="admin-surface flex min-w-0 flex-col p-5 sm:p-6">
                <p className="admin-eyebrow">SIGNALS</p>
                <h2 className="mt-2 text-lg font-bold tracking-tight">运行信号</h2>
                <div className="mt-6 space-y-5">
                  <div>
                    <div className="flex justify-between text-xs"><span className="text-muted-foreground">失败率</span><span className="font-mono">{data.summary.runs ? ((data.summary.failed_runs / data.summary.runs) * 100).toFixed(1) : '0.0'}%</span></div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-destructive" style={{ width: `${data.summary.runs ? Math.min(100, (data.summary.failed_runs / data.summary.runs) * 100) : 0}%` }} /></div>
                  </div>
                  <div>
                    <div className="flex justify-between text-xs"><span className="text-muted-foreground">活跃率</span><span className="font-mono">{data.summary.users ? Math.round((data.summary.active_users / data.summary.users) * 100) : 0}%</span></div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${data.summary.users ? (data.summary.active_users / data.summary.users) * 100 : 0}%` }} /></div>
                  </div>
                </div>
                <div className="mt-auto pt-7">
                  <div className="flex items-center gap-2 border-t pt-4 text-xs text-muted-foreground"><Clock3 size={14} /> 数据每 60 秒自动刷新</div>
                </div>
              </section>
            </div>

            <section className="admin-surface mt-4 overflow-hidden">
              <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex gap-1 rounded-lg bg-muted/70 p-1">
                  <button type="button" onClick={() => setTab('records')} className={`rounded-md px-3 py-1.5 text-xs font-semibold ${tab === 'records' ? 'bg-card shadow-sm' : 'text-muted-foreground'}`}>运行记录 <span className="ml-1 font-mono">{data.records.length}</span></button>
                  <button type="button" onClick={() => setTab('users')} className={`rounded-md px-3 py-1.5 text-xs font-semibold ${tab === 'users' ? 'bg-card shadow-sm' : 'text-muted-foreground'}`}>用户 <span className="ml-1 font-mono">{data.users.length}</span></button>
                  <button type="button" onClick={() => setTab('feedback')} className={`rounded-md px-3 py-1.5 text-xs font-semibold ${tab === 'feedback' ? 'bg-card shadow-sm' : 'text-muted-foreground'}`}>反馈 {feedback.data && <span className="ml-1 font-mono">{feedback.data.feedback.length}</span>}</button>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                  {tab === 'feedback' && (
                    <select
                      value={rating}
                      onChange={(event) => setRating(event.target.value as 'all' | FeedbackRating)}
                      aria-label="按评价筛选"
                      className="h-9 rounded-lg border bg-background px-3 text-xs text-foreground outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/10"
                    >
                      <option value="all">全部</option>
                      <option value="satisfied">满意</option>
                      <option value="unsatisfied">不满意</option>
                    </select>
                  )}
                  <label className="flex h-9 items-center gap-2 rounded-lg border bg-background px-3 text-muted-foreground focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/10 sm:w-72">
                    <Search size={14} />
                    <input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder={tab === 'records' ? '搜索题目、用户或模型' : tab === 'users' ? '搜索手机号或用户 ID' : '搜索用户、题目或评论'}
                      className="min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground"
                    />
                  </label>
                </div>
              </div>

              {tab === 'records' ? (
                <div>
                  <div className="hidden grid-cols-[minmax(0,1fr)_120px_120px_110px_100px_84px_32px] gap-4 border-t bg-muted/35 px-5 py-2.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground lg:grid">
                    <span>题目 / Session</span><span>用户</span><span>模型</span><span>Token</span><span>耗时</span><span>状态</span><span />
                  </div>
                  {filteredRecords.map((record) => <RunDetails key={record.id} record={record} />)}
                  {!filteredRecords.length && <p className="border-t px-5 py-12 text-center text-sm text-muted-foreground">没有匹配的运行记录</p>}
                </div>
              ) : tab === 'users' ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[780px] text-left text-sm">
                    <thead className="border-t bg-muted/35 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                      <tr><th className="px-5 py-2.5 font-medium">用户</th><th className="px-4 py-2.5 font-medium">最近活跃</th><th className="px-4 py-2.5 text-right font-medium">解题</th><th className="px-4 py-2.5 text-right font-medium">输入</th><th className="px-4 py-2.5 text-right font-medium">输出</th><th className="px-5 py-2.5 text-right font-medium">总 token</th></tr>
                    </thead>
                    <tbody>
                      {filteredUsers.map((item) => (
                        <tr key={item.id} className="border-t transition-colors hover:bg-accent/30">
                          <td className="px-5 py-3.5"><p className="font-mono text-xs font-semibold">{item.phone}</p><p className="mt-0.5 font-mono text-[9px] text-muted-foreground">{item.id}</p></td>
                          <td className="px-4 py-3.5 text-xs text-muted-foreground">{dateTime(item.last_active_at || item.last_login_at)}</td>
                          <td className="px-4 py-3.5 text-right font-mono text-xs">{number.format(item.runs)}</td>
                          <td className="px-4 py-3.5 text-right font-mono text-xs text-muted-foreground">{compactNumber(item.input_tokens)}</td>
                          <td className="px-4 py-3.5 text-right font-mono text-xs text-muted-foreground">{compactNumber(item.output_tokens)}</td>
                          <td className="px-5 py-3.5 text-right font-mono text-xs font-semibold">{compactNumber(item.total_tokens)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!filteredUsers.length && <p className="border-t px-5 py-12 text-center text-sm text-muted-foreground">没有匹配的用户</p>}
                </div>
              ) : (
                <div className="overflow-x-auto">
                  {feedback.isLoading ? (
                    <div className="flex items-center justify-center gap-3 border-t px-5 py-12 text-sm text-muted-foreground">
                      <RefreshCw size={16} className="animate-spin" /> 正在读取反馈
                    </div>
                  ) : feedback.isError ? (
                    <div className="flex items-center justify-center gap-2 border-t px-5 py-12 text-sm text-destructive">
                      <CircleAlert size={16} /> 暂时无法读取反馈
                    </div>
                  ) : (
                    <>
                      <table className="w-full min-w-[980px] text-left text-sm">
                        <thead className="border-t bg-muted/35 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                          <tr><th className="px-5 py-2.5 font-medium">时间</th><th className="px-4 py-2.5 font-medium">用户</th><th className="px-4 py-2.5 font-medium">评价</th><th className="px-4 py-2.5 font-medium">结果</th><th className="px-4 py-2.5 font-medium">题目</th><th className="px-5 py-2.5 font-medium">评论</th></tr>
                        </thead>
                        <tbody>
                          {filteredFeedback.map((item) => (
                            <tr key={item.id} className="border-t align-top transition-colors hover:bg-accent/30">
                              <td className="whitespace-nowrap px-5 py-3.5 font-mono text-xs text-muted-foreground">{dateTime(item.created_at)}</td>
                              <td className="px-4 py-3.5"><p className="text-xs font-semibold">{item.label || '未知用户'}</p><p className="mt-0.5 font-mono text-[9px] text-muted-foreground">{item.user_id}</p></td>
                              <td className="px-4 py-3.5"><RatingBadge rating={item.rating} /></td>
                              <td className="px-4 py-3.5"><StatusBadge status={item.outcome} /></td>
                              <td className="max-w-sm px-4 py-3.5 text-xs leading-5">{item.problem_preview || '—'}</td>
                              <td className="max-w-sm whitespace-pre-wrap px-5 py-3.5 text-xs leading-5 text-muted-foreground">{item.comment || '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {!filteredFeedback.length && <p className="border-t px-5 py-12 text-center text-sm text-muted-foreground">没有匹配的反馈</p>}
                    </>
                  )}
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}
