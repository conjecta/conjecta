// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const moduleLoads = vi.hoisted(() => ({
  knowledge: vi.fn(),
}));

vi.mock('../components/ExplorerPanel', () => ({
  ExplorerPanel: () => <div>Explorer ready</div>,
}));
vi.mock('../components/KnowledgePanel', () => {
  moduleLoads.knowledge();
  return { KnowledgePanel: () => <div>Knowledge ready</div> };
});
import { SidePanel } from '../components/SidePanel';
import { useUiStore } from '../store/ui';

describe('SidePanel lazy loading', () => {
  it('keeps inactive heavy panels unloaded and shows a suspense fallback', async () => {
    useUiStore.setState({ activePanel: 'explorer', workbenchCollapsed: false });

    expect(moduleLoads.knowledge).not.toHaveBeenCalled();

    render(<SidePanel />);
    expect(screen.getByText('Explorer ready')).toBeInTheDocument();

    act(() => useUiStore.setState({ activePanel: 'knowledge' }));

    expect(screen.getByRole('status')).toHaveTextContent('正在加载');
    await waitFor(() => expect(moduleLoads.knowledge).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Knowledge ready')).toBeInTheDocument();
  });
});
