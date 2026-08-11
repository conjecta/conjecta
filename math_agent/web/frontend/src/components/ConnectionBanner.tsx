import { QUOTA_EXCEEDED_MESSAGE, isQuotaExceededMessage } from '@/lib/publicError';
import { useUiStore } from '@/store/ui';

export function ConnectionBanner({ message, onClose }: { message: string; onClose: () => void }) {
  const openUsageDialog = useUiStore((state) => state.openUsageDialog);
  const isQuota = isQuotaExceededMessage(message);

  if (isQuota) {
    return (
      <div
        role="alert"
        className="flex flex-wrap items-center justify-between gap-3 border-b border-amber-500/40 bg-amber-500/15 px-4 py-3 text-sm text-amber-950 dark:text-amber-50"
      >
        <div className="min-w-0 flex-1 space-y-0.5">
          <p className="font-semibold">今日免费额度已用完</p>
          <p className="text-xs opacity-90">{QUOTA_EXCEEDED_MESSAGE}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => openUsageDialog('quota_exceeded')}
            className="rounded-md border border-amber-600/40 bg-card px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-secondary"
          >
            去绑定 API Key
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭提示"
            className="px-1 text-base font-bold opacity-70 hover:opacity-100"
          >
            ×
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      role="alert"
      className="flex items-center justify-between border-b bg-destructive/10 px-4 py-2 text-xs text-destructive"
    >
      <span>{message}</span>
      <button type="button" onClick={onClose} aria-label="关闭提示" className="font-bold">
        ×
      </button>
    </div>
  );
}
