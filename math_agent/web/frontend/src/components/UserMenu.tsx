import { useEffect, useRef, useState } from 'react';
import { BarChart3, BrainCircuit, LogOut, Settings } from 'lucide-react';
import { avatarColor, avatarInitials } from '@/lib/avatar';
import { useAuthStore } from '@/store/auth';
import { useUiStore } from '@/store/ui';
import { MemoryDialog } from '@/components/MemoryDialog';

export function UserMenu() {
  const { user, phoneAuthEnabled, logout } = useAuthStore();
  const openUsageDialog = useUiStore((state) => state.openUsageDialog);
  const [open, setOpen] = useState(false);
  const [showMemories, setShowMemories] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  if (!phoneAuthEnabled || !user) return null;

  const initials = avatarInitials(user.phone);
  const color = avatarColor(user.id);

  return (
    <div ref={rootRef} className="relative ml-1 border-l pl-2">
      <button
        type="button"
        aria-label="账号菜单"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-lg px-1 py-0.5 hover:bg-secondary"
      >
        <span
          className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold text-white ${color}`}
        >
          {initials}
        </span>
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-56 rounded-xl border bg-popover p-1.5 shadow-xl">
          <div className="border-b px-3 py-2">
            <p className="truncate text-sm font-medium">{user.phone}</p>
            <p className="truncate font-mono text-[10px] text-muted-foreground">{user.id}</p>
          </div>
          {user.is_admin && (
            <a
              href="/admin"
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-secondary"
              onClick={() => setOpen(false)}
            >
              <BarChart3 size={14} />
              运营中心
            </a>
          )}
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-secondary"
            onClick={() => {
              setOpen(false);
              setShowMemories(true);
            }}
          >
            <BrainCircuit size={14} />
            记忆管理
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-secondary"
            onClick={() => {
              setOpen(false);
              openUsageDialog();
            }}
          >
            <Settings size={14} />
            用量与 API Key
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-destructive hover:bg-secondary"
            onClick={() => {
              setOpen(false);
              logout();
            }}
          >
            <LogOut size={14} />
            退出登录
          </button>
        </div>
      )}
      {showMemories && <MemoryDialog onClose={() => setShowMemories(false)} />}
    </div>
  );
}
