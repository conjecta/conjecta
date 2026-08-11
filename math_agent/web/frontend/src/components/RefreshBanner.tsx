import { X } from 'lucide-react';

export function RefreshBanner({
  onRefresh,
  onClose,
}: {
  onRefresh?: () => void;
  onClose?: () => void;
}) {
  const handleRefresh = () => {
    if (onRefresh) {
      onRefresh();
    } else {
      window.location.reload();
    }
  };

  return (
    <div
      role="status"
      className="fixed bottom-4 right-4 z-50 w-[260px] rounded-xl border border-border bg-card p-3 shadow-lg duration-300 animate-in fade-in slide-in-from-bottom-2"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs leading-relaxed text-card-foreground">
          系统已更新，刷新页面以使用最新版本。
        </p>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭提示"
            className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <X size={14} />
          </button>
        )}
      </div>
      <div className="mt-2 flex justify-end">
        <button
          type="button"
          onClick={handleRefresh}
          className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          刷新页面
        </button>
      </div>
    </div>
  );
}
