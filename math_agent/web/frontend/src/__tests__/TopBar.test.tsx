// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TopBar } from '../components/TopBar';
import { useAuthStore } from '../store/auth';

vi.mock('@/components/MemoryDialog', () => ({
  MemoryDialog: ({ onClose }: { onClose: () => void }) => (
    <div role="dialog" aria-label="记忆管理">
      <button type="button" onClick={onClose}>
        关闭记忆
      </button>
    </div>
  ),
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const phoneUser = { id: 'u1', phone: '13800000000', is_admin: false };

describe('TopBar', () => {
  afterEach(() => {
    cleanup();
    useAuthStore.setState({ user: null, phoneAuthEnabled: false });
    window.history.pushState({}, '', '/');
    localStorage.removeItem('conjecta-theme');
    document.documentElement.classList.remove('dark');
  });

  it('links Conjecta logo to the marketing homepage', () => {
    renderWithClient(<TopBar />);
    const logo = screen.getByRole('link', { name: '返回网站首页' });
    expect(logo).toHaveAttribute('href', '/');
    expect(logo).toHaveTextContent('Conjecta');
  });

  it('does not duplicate sidebar panel switches in the top bar', () => {
    renderWithClient(<TopBar />);
    expect(screen.queryByRole('button', { name: '对话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开知识库' })).not.toBeInTheDocument();
  });

  it('shows a back-to-workbench link instead of the sidebar toggle on sub-pages', () => {
    window.history.pushState({}, '', '/app/inbox');
    renderWithClient(<TopBar />);
    expect(screen.getByRole('link', { name: '返回工作台' })).toHaveAttribute('href', '/app');
    expect(screen.queryByRole('button', { name: '收起工作区' })).not.toBeInTheDocument();
  });

  it('shows the sidebar toggle and no back link on the workbench home', () => {
    window.history.pushState({}, '', '/app');
    renderWithClient(<TopBar />);
    expect(screen.getByRole('button', { name: '收起工作区' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '返回工作台' })).not.toBeInTheDocument();
  });

  it('opens memory management from the account menu when logged in', () => {
    useAuthStore.setState({ phoneAuthEnabled: true, user: phoneUser });
    renderWithClient(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: '账号菜单' }));
    fireEvent.click(screen.getByRole('button', { name: '记忆管理' }));
    expect(screen.getByRole('dialog', { name: '记忆管理' })).toBeInTheDocument();
  });

  it('hides the friends link when phone auth is on and logged out', () => {
    useAuthStore.setState({ phoneAuthEnabled: true, user: null });
    renderWithClient(<TopBar />);
    expect(screen.queryByRole('link', { name: '好友' })).not.toBeInTheDocument();
  });

  it('shows the friends link in local mode without a login', () => {
    useAuthStore.setState({ phoneAuthEnabled: false, user: null });
    renderWithClient(<TopBar />);
    expect(screen.getByRole('link', { name: '好友' })).toHaveAttribute('href', '/app/friends');
    expect(screen.queryByRole('link', { name: '研究' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /决策收件箱/ })).not.toBeInTheDocument();
  });

  describe('active route highlight', () => {
    it('marks the friends link as current on the friends page', () => {
      window.history.pushState({}, '', '/app/friends');
      renderWithClient(<TopBar />);
      expect(screen.getByRole('link', { name: '好友' })).toHaveAttribute('aria-current', 'page');
    });
  });

  describe('theme menu', () => {
    it('opens a menu with the three theme options', () => {
      renderWithClient(<TopBar />);
      fireEvent.click(screen.getByRole('button', { name: '主题设置' }));
      expect(screen.getByRole('menuitemradio', { name: /跟随系统/ })).toBeInTheDocument();
      expect(screen.getByRole('menuitemradio', { name: /浅色/ })).toBeInTheDocument();
      expect(screen.getByRole('menuitemradio', { name: /深色/ })).toBeInTheDocument();
    });

    it('switches to dark theme from the menu and closes it', () => {
      renderWithClient(<TopBar />);
      fireEvent.click(screen.getByRole('button', { name: '主题设置' }));
      fireEvent.click(screen.getByRole('menuitemradio', { name: /深色/ }));
      expect(localStorage.getItem('conjecta-theme')).toBe('dark');
      expect(document.documentElement.classList.contains('dark')).toBe(true);
      expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    });

    it('marks the current theme as checked', () => {
      localStorage.setItem('conjecta-theme', 'light');
      renderWithClient(<TopBar />);
      fireEvent.click(screen.getByRole('button', { name: '主题设置' }));
      expect(screen.getByRole('menuitemradio', { name: /浅色/ })).toHaveAttribute('aria-checked', 'true');
      expect(screen.getByRole('menuitemradio', { name: /跟随系统/ })).toHaveAttribute('aria-checked', 'false');
    });

    it('closes the menu on outside pointer down', () => {
      renderWithClient(<TopBar />);
      fireEvent.click(screen.getByRole('button', { name: '主题设置' }));
      expect(screen.getByRole('menuitemradio', { name: /深色/ })).toBeInTheDocument();
      fireEvent.mouseDown(document.body);
      expect(screen.queryByRole('menuitemradio', { name: /深色/ })).not.toBeInTheDocument();
    });
  });

  it('exposes tooltip titles on the top-right controls', () => {
    useAuthStore.setState({ phoneAuthEnabled: true, user: phoneUser });
    renderWithClient(<TopBar />);
    expect(screen.getByRole('link', { name: '好友' })).toHaveAttribute('title');
    expect(screen.getByRole('button', { name: '主题设置' })).toHaveAttribute('title');
  });

  it('keeps secondary knowledge entries off the top bar', () => {
    useAuthStore.setState({ phoneAuthEnabled: false, user: null });
    renderWithClient(<TopBar />);
    expect(screen.queryByRole('button', { name: '更多入口' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '知识' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '好友知识' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '好友' })).toHaveAttribute('href', '/app/friends');
  });
});
