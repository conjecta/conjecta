import { describe, it, expect, beforeEach } from 'vitest';
import { useUiStore } from '../store/ui';

describe('uiStore', () => {
  beforeEach(() => {
    useUiStore.setState({
      activePanel: 'explorer',
      workbenchCollapsed: false,
      selectedProjectId: 'default',
      selectedConversationId: null,
      chatResetKey: 0,
      selectedKnowledgeTab: 'knowledge',
    });
  });

  it('defaults to explorer panel', () => {
    expect(useUiStore.getState().activePanel).toBe('explorer');
  });
  it('sets active panel', () => {
    useUiStore.getState().setActivePanel('knowledge');
    expect(useUiStore.getState().activePanel).toBe('knowledge');
  });
  it('toggles workbench collapse', () => {
    useUiStore.getState().toggleWorkbenchCollapse();
    expect(useUiStore.getState().workbenchCollapsed).toBe(true);
  });
  it('starts a new chat', () => {
    useUiStore.getState().setSelectedConversationId('conversation-1');
    const resetKey = useUiStore.getState().chatResetKey;
    useUiStore.getState().startNewChat();
    expect(useUiStore.getState().selectedConversationId).toBeNull();
    expect(useUiStore.getState().chatResetKey).toBe(resetKey + 1);
  });
  it('returns home from top bar action', () => {
    useUiStore.setState({
      activePanel: 'knowledge',
      workbenchCollapsed: true,
      selectedConversationId: 'conversation-1',
      chatResetKey: 0,
    });
    useUiStore.getState().goHome();
    const state = useUiStore.getState();
    expect(state.activePanel).toBe('explorer');
    expect(state.workbenchCollapsed).toBe(false);
    expect(state.selectedConversationId).toBeNull();
    expect(state.chatResetKey).toBe(1);
  });
});
