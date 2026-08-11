import { CLOUD_STORAGE_REQUIRED_MESSAGE } from '@/lib/publicError';

export function CloudSetupNotice({
  title = '需要配置云端存储',
}: {
  title?: string;
}) {
  return (
    <div
      role="status"
      className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm leading-relaxed text-foreground"
    >
      <p className="font-medium">{title}</p>
      <p className="mt-1 text-muted-foreground">{CLOUD_STORAGE_REQUIRED_MESSAGE}</p>
      <ol className="mt-3 list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
        <li>复制 <code className="font-mono">.env.example</code> 为 <code className="font-mono">.env</code></li>
        <li>
          填入 <code className="font-mono">SUPABASE_URL</code> 与{' '}
          <code className="font-mono">SUPABASE_SERVICE_ROLE_KEY</code>
        </li>
        <li>
          在 Supabase SQL Editor 执行{' '}
          <code className="font-mono">docs/supabase_social_collab_schema.sql</code>
        </li>
        <li>重启 <code className="font-mono">math-agent-web</code> 后刷新本页</li>
      </ol>
    </div>
  );
}
