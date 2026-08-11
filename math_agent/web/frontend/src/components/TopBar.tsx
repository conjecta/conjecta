import { useEffect, useRef, useState } from 'react';
import { useTheme } from '@/hooks/useTheme';
import { useUiStore } from '@/store/ui';
import { useAuthStore } from '@/store/auth';
import {
  ArrowLeft,
  Check,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Sun,
  UserPlus,
} from 'lucide-react';
import { BrandMark } from '@/components/BrandMark';
import { UserMenu } from '@/components/UserMenu';
import { UsageDialog } from '@/components/UsageDialog';

type Theme = 'light' | 'dark' | 'system';

const THEME_ORDER: Theme[] = ['system', 'light', 'dark'];
const THEME_META: Record<Theme, { icon: typeof Monitor; label: string }> = {
  system: { icon: Monitor, label: '跟随系统' },
  light: { icon: Sun, label: '浅色' },
  dark: { icon: Moon, label: '深色' },
};

const NAV_LINK_BASE =
  'flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium transition-colors hover:bg-accent hover:text-foreground';

function navLinkClass(active: boolean): string {
  return `${NAV_LINK_BASE} ${active ? 'bg-accent text-foreground' : 'text-muted-foreground'}`;
}

export function TopBar() {
  const { theme, setTheme } = useTheme();
  const {
    workbenchCollapsed,
    toggleWorkbenchCollapse,
    usageDialogOpen,
    usageDialogReason,
    closeUsageDialog,
  } = useUiStore();
  const { user, phoneAuthEnabled } = useAuthStore();
  const [showThemeMenu, setShowThemeMenu] = useState(false);
  const themeMenuRef = useRef<HTMLDivElement>(null);
  const current = (THEME_ORDER.includes(theme as Theme) ? theme : 'system') as Theme;
  const { icon: ThemeIcon } = THEME_META[current];
  // Entries backed by authenticated APIs: hidden exactly when they would 401.
  // With phone auth disabled the backend serves a local-dev sentinel user.
  const canUseAuthedFeatures = !phoneAuthEnabled || Boolean(user);

  const pathname = window.location.pathname;
  const friendsActive = pathname === '/app/friends';
  // Sub-pages (research, inbox, knowledge, ...) get a way back to the workbench;
  // the sidebar toggle only does something on the workbench home itself.
  const onSubPage = pathname.startsWith('/app/') && pathname !== '/app/';

  useEffect(() => {
    if (!showThemeMenu) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!themeMenuRef.current?.contains(event.target as Node)) setShowThemeMenu(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [showThemeMenu]);

  return (
    <>
      <header className="z-30 flex h-14 shrink-0 items-center justify-between border-b bg-card/90 px-3 backdrop-blur-xl sm:px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          {onSubPage ? (
            <a
              href="/app"
              aria-label="返回工作台"
              title="返回工作台"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <ArrowLeft size={18} />
            </a>
          ) : (
            <button
              type="button"
              aria-label={workbenchCollapsed ? '打开工作区' : '收起工作区'}
              aria-expanded={!workbenchCollapsed}
              onClick={toggleWorkbenchCollapse}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              {workbenchCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            </button>
          )}
          <a
            href="/"
            aria-label="返回网站首页"
            className="group flex min-w-0 items-center gap-2.5 rounded-xl py-1 pl-1 pr-2 transition-colors hover:bg-accent/50"
          >
            <BrandMark className="h-[26px] w-[26px] shrink-0 transition-transform duration-300 group-hover:-rotate-[8deg]" />
            <span className="flex min-w-0 items-baseline gap-2">
              <span className="font-display text-[20px] leading-none tracking-[-0.012em] text-foreground">
                Conjecta
              </span>
              <span className="hidden shrink-0 items-center gap-2 text-[11px] font-normal italic leading-none text-muted-foreground md:inline-flex">
                <span aria-hidden="true" className="h-3 w-px bg-border" />
                proof workbench
              </span>
            </span>
          </a>
        </div>

        <div className="flex items-center gap-1">
          {canUseAuthedFeatures && (
            <a
              href="/app/friends"
              aria-current={friendsActive ? 'page' : undefined}
              title="好友"
              className={navLinkClass(friendsActive)}
            >
              <UserPlus size={14} />
              <span className="hidden sm:inline">好友</span>
            </a>
          )}
          <div ref={themeMenuRef} className="relative">
            <button
              type="button"
              aria-label="主题设置"
              title="主题设置"
              aria-haspopup="menu"
              aria-expanded={showThemeMenu}
              onClick={() => setShowThemeMenu((v) => !v)}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <ThemeIcon size={15} />
            </button>
            {showThemeMenu && (
              <div
                role="menu"
                aria-label="主题"
                className="absolute right-0 top-full z-50 mt-2 w-40 rounded-xl border bg-popover p-1.5 shadow-xl"
              >
                {THEME_ORDER.map((option) => {
                  const { icon: Icon, label: optionLabel } = THEME_META[option];
                  return (
                    <button
                      key={option}
                      type="button"
                      role="menuitemradio"
                      aria-checked={current === option}
                      onClick={() => {
                        setTheme(option);
                        setShowThemeMenu(false);
                      }}
                      className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-secondary"
                    >
                      <Icon size={14} />
                      <span className="flex-1">{optionLabel}</span>
                      {current === option && <Check size={14} className="text-primary" />}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <UserMenu />
        </div>
      </header>
      {usageDialogOpen && (
        <UsageDialog reason={usageDialogReason} onClose={closeUsageDialog} />
      )}
    </>
  );
}
