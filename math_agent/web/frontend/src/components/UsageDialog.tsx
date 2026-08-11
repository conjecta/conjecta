import { useEffect, useState } from 'react';
import {
  deleteApiKey,
  fetchApiKey,
  fetchUsage,
  setApiKey as saveApiKey,
  type ApiKeyInfo,
  type UsageSummary,
} from '@/api/billing';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const PROVIDERS = [
  { value: 'openai', label: 'OpenAI' },
];

export function UsageDialog({
  onClose,
  reason = 'default',
}: {
  onClose: () => void;
  reason?: 'default' | 'quota_exceeded';
}) {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [apiKey, setApiKey] = useState<ApiKeyInfo | null>(null);
  const [provider, setProvider] = useState('openai');
  const [inputKey, setInputKey] = useState('');
  const [error, setError] = useState<string | null>(null);
  const quotaExceeded = reason === 'quota_exceeded';

  useEffect(() => {
    let mounted = true;
    setError(null);

    fetchUsage()
      .then((data) => {
        if (mounted) setUsage(data);
      })
      .catch((err) => {
        if (mounted) {
          setError(err instanceof Error ? err.message : 'Failed to load usage');
        }
      });

    fetchApiKey()
      .then((data) => {
        if (mounted) setApiKey(data);
      })
      .catch((err) => {
        if (mounted) {
          setError(err instanceof Error ? err.message : 'Failed to load API key');
        }
      });

    return () => {
      mounted = false;
    };
  }, []);

  const handleSave = async () => {
    const trimmed = inputKey.trim();
    if (!trimmed) return;
    setError(null);
    try {
      const info = await saveApiKey(provider, trimmed);
      setApiKey(info);
      setInputKey('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save API key');
    }
  };

  const handleDelete = async () => {
    setError(null);
    try {
      await deleteApiKey();
      setApiKey(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete API key');
    }
  };

  const progressPercent =
    usage && usage.today.quota_tokens > 0
      ? Math.min(100, (usage.today.total_tokens / usage.today.quota_tokens) * 100)
      : 0;
  const progressWidth = `${progressPercent}%`;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>用量与 API Key</DialogTitle>
          <DialogDescription>
            {quotaExceeded
              ? '今日免费额度已用完，绑定自己的 API Key 后可继续解题'
              : '管理 Token 用量和 API Key'}
          </DialogDescription>
        </DialogHeader>

        {quotaExceeded && (
          <div
            role="alert"
            className="rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-2.5 text-sm text-amber-950 dark:text-amber-50"
          >
            <p className="font-semibold">今日免费额度已用完</p>
            <p className="mt-1 text-xs opacity-90">
              绑定 OpenAI API Key 后，将按你自己的额度计费，不再占用平台免费配额。
            </p>
          </div>
        )}

        {error && (
          <div
            role="alert"
            className="rounded border border-destructive bg-destructive/10 px-3 py-2 text-xs text-destructive"
          >
            {error}
          </div>
        )}

        {usage && (
          <div className="space-y-2 text-sm">
            <div>
              今日 Token:{' '}
              {usage.unlimited_quota || usage.today.quota_tokens <= 0
                ? `${usage.today.total_tokens.toLocaleString()} / 不限`
                : `${usage.today.total_tokens.toLocaleString()} / ${usage.today.quota_tokens.toLocaleString()}`}
            </div>
            <div>今日成本: ${usage.today.cost_usd.toFixed(4)}</div>
            <div>本月 Token: {usage.this_month.total_tokens.toLocaleString()}</div>
            <div>本月成本: ${usage.this_month.cost_usd.toFixed(4)}</div>
            <div className="h-2 w-full rounded bg-muted">
              <div
                className="h-2 rounded bg-primary"
                role="progressbar"
                aria-valuenow={progressPercent}
                aria-valuemin={0}
                aria-valuemax={100}
                style={{ width: progressWidth }}
              />
            </div>
          </div>
        )}

        <div className="space-y-3">
          <Select value={provider} onValueChange={setProvider}>
            <SelectTrigger className="w-full" aria-label="选择 API 提供商">
              <SelectValue placeholder="选择提供商" />
            </SelectTrigger>
            <SelectContent>
              {PROVIDERS.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Input
            type="password"
            placeholder="输入 API key"
            value={inputKey}
            onChange={(e) => setInputKey(e.target.value)}
          />

          <div className="flex gap-2">
            <Button type="button" onClick={handleSave} disabled={!inputKey.trim()}>
              保存
            </Button>
            {apiKey && (
              <Button type="button" variant="outline" onClick={handleDelete}>
                删除已绑定 key
              </Button>
            )}
          </div>

          {apiKey && (
            <p className="text-xs text-muted-foreground">
              已绑定 {apiKey.provider}，更新于 {new Date(apiKey.updated_at).toLocaleString()}
            </p>
          )}
        </div>

        <div className="flex justify-end">
          <Button type="button" variant="ghost" onClick={onClose}>
            关闭
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
