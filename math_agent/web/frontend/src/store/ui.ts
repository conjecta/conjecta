import { create } from 'zustand';

export type Panel = 'explorer' | 'knowledge';
export type UsageDialogReason = 'default' | 'quota_exceeded';

interface UiState {
  activePanel: Panel;
  workbenchCollapsed: boolean;
  selectedProjectId: string;
  selectedOwnerUserId: string | null;
  selectedConversationId: string | null;
  chatResetKey: number;
  selectedKnowledgeTab: string;
  draftPrompt: string;
  draftPromptKey: number;
  usageDialogOpen: boolean;
  usageDialogReason: UsageDialogReason;
  resultDrawerTurnId: string | null;
  setActivePanel: (panel: Panel) => void;
  toggleWorkbenchCollapse: () => void;
  setSelectedProjectId: (id: string, ownerUserId?: string | null) => void;
  setSelectedConversationId: (id: string | null) => void;
  startNewChat: () => void;
  goHome: () => void;
  setSelectedKnowledgeTab: (tab: string) => void;
  setDraftPrompt: (text: string) => void;
  openUsageDialog: (reason?: UsageDialogReason) => void;
  closeUsageDialog: () => void;
  openResultDrawer: (turnId: string) => void;
  closeResultDrawer: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  activePanel: 'explorer',
  workbenchCollapsed: false,
  selectedProjectId: 'default',
  selectedOwnerUserId: null,
  selectedConversationId: null,
  chatResetKey: 0,
  selectedKnowledgeTab: 'knowledge',
  draftPrompt: '',
  draftPromptKey: 0,
  usageDialogOpen: false,
  usageDialogReason: 'default',
  resultDrawerTurnId: null,
  setActivePanel: (panel) => set({ activePanel: panel }),
  toggleWorkbenchCollapse: () =>
    set((state) => ({ workbenchCollapsed: !state.workbenchCollapsed })),
  setSelectedProjectId: (id, ownerUserId = null) =>
    set({
      selectedProjectId: id,
      selectedOwnerUserId: ownerUserId ?? null,
      selectedConversationId: null,
    }),
  setSelectedConversationId: (id) =>
    set((state) => ({
      selectedConversationId: id,
      chatResetKey: state.chatResetKey + 1,
    })),
  startNewChat: () =>
    set((state) => ({
      selectedConversationId: null,
      chatResetKey: state.chatResetKey + 1,
    })),
  goHome: () =>
    set((state) => ({
      activePanel: 'explorer',
      workbenchCollapsed: false,
      selectedConversationId: null,
      chatResetKey: state.chatResetKey + 1,
    })),
  setSelectedKnowledgeTab: (tab) => set({ selectedKnowledgeTab: tab }),
  setDraftPrompt: (text) =>
    set((state) => ({ draftPrompt: text, draftPromptKey: state.draftPromptKey + 1 })),
  openUsageDialog: (reason = 'default') =>
    set({ usageDialogOpen: true, usageDialogReason: reason }),
  closeUsageDialog: () =>
    set({ usageDialogOpen: false, usageDialogReason: 'default' }),
  openResultDrawer: (turnId) => set({ resultDrawerTurnId: turnId }),
  closeResultDrawer: () => set({ resultDrawerTurnId: null }),
}));
