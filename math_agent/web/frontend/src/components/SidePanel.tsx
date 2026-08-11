import { lazy, Suspense } from 'react';
import { BookOpen, History, X } from 'lucide-react';
import { useUiStore } from '@/store/ui';
import { ExplorerPanel } from './ExplorerPanel';

const KnowledgePanel = lazy(() => import('./KnowledgePanel').then((module) => ({
  default: module.KnowledgePanel,
})));

function PanelFallback() {
  return (
    <div role="status" aria-live="polite" className="flex flex-1 items-center justify-center text-xs text-muted-foreground">
      正在加载…
    </div>
  );
}

export function SidePanel() {
  const { activePanel, setActivePanel, workbenchCollapsed, toggleWorkbenchCollapse } = useUiStore();
  if (workbenchCollapsed) return null;

  return (
    <>
      <button
        type="button"
        aria-label="关闭工作区"
        className="absolute inset-0 z-10 bg-foreground/20 backdrop-blur-[1px] md:hidden"
        onClick={toggleWorkbenchCollapse}
      />
      <aside className="absolute inset-y-0 left-0 z-20 flex w-[min(88vw,320px)] shrink-0 flex-col overflow-hidden border-r bg-card shadow-2xl md:relative md:w-[308px] md:shadow-none">
        <div className="flex h-12 items-center gap-1 border-b px-3">
          <button
            type="button"
            onClick={() => setActivePanel('explorer')}
            aria-pressed={activePanel === 'explorer'}
            className={`flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg text-xs font-semibold transition-colors ${activePanel === 'explorer' ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'}`}
          >
            <History size={14} /> 对话
          </button>
          <button
            type="button"
            onClick={() => setActivePanel('knowledge')}
            aria-pressed={activePanel === 'knowledge'}
            className={`flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg text-xs font-semibold transition-colors ${activePanel === 'knowledge' ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'}`}
          >
            <BookOpen size={14} /> 知识库
          </button>
          <button type="button" aria-label="收起工作区" onClick={toggleWorkbenchCollapse} className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground">
            <X size={15} />
          </button>
        </div>
        <Suspense fallback={<PanelFallback />}>
          {activePanel === 'knowledge' ? <KnowledgePanel /> : <ExplorerPanel />}
        </Suspense>
      </aside>
    </>
  );
}
