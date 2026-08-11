// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { KnowledgeHubPage } from '@/components/KnowledgeHubPage';
import { PublicGalleryPage } from '@/components/PublicGalleryPage';
import { CardDetailPage } from '@/components/CardDetailPage';
import { FriendsPage } from '@/components/FriendsPage';
import { useAuthStore } from '@/store/auth';

vi.mock('@/api/knowledgeCards', () => ({
  listMyCards: vi.fn(async () => ({ ok: true, cards: [] })),
  listPublicCards: vi.fn(async () => ({ ok: true, cards: [] })),
  listFriendCards: vi.fn(async () => ({ ok: true, cards: [] })),
  getCard: vi.fn(async () => ({
    ok: true,
    card: {
      card: { visibility: 'public', status: 'published', star_count: 0 },
      revision: { title: '测试卡片', statement: 'P => Q', body: '证明略' },
    },
  })),
  exportCard: vi.fn(),
}));

vi.mock('@/api/auth', () => ({
  fetchAuthConfig: vi.fn(async () => ({ ok: true, cloud_storage_configured: true })),
  fetchMe: vi.fn(async () => ({ ok: true, user: null })),
  logout: vi.fn(async () => ({ ok: true })),
}));

vi.mock('@/api/friends', () => ({
  getMyProfile: vi.fn(async () => ({ ok: true, profile: { display_name: 'Tester' } })),
  listFriends: vi.fn(async () => ({ ok: true, friends: [] })),
  listFriendRequests: vi.fn(async () => ({ ok: true, incoming: [], outgoing: [] })),
  requestFriend: vi.fn(),
  acceptFriendRequest: vi.fn(),
  declineFriendRequest: vi.fn(),
  unfriend: vi.fn(),
  updateMyProfile: vi.fn(),
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('knowledge pages chrome', () => {
  afterEach(() => {
    cleanup();
    useAuthStore.setState({ user: null, phoneAuthEnabled: false });
  });

  it('knowledge hub renders the top bar so users can navigate back', async () => {
    renderWithClient(<KnowledgeHubPage />);
    expect(await screen.findByRole('link', { name: '返回网站首页' })).toBeInTheDocument();
  });

  it('public gallery renders the top bar', async () => {
    renderWithClient(<PublicGalleryPage />);
    expect(await screen.findByRole('link', { name: '返回网站首页' })).toBeInTheDocument();
  });

  it('card detail renders the top bar and keeps the gallery back link', async () => {
    renderWithClient(<CardDetailPage cardId="card-1" />);
    expect(await screen.findByRole('link', { name: '返回网站首页' })).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: '← 返回画廊' })).toHaveAttribute(
      'href',
      '/app/knowledge/gallery',
    );
  });

  it('friends page hosts 好友 / 好友知识 / 我的分享 tabs and honors the legacy initial tab', async () => {
    renderWithClient(<FriendsPage initialTab="share" />);
    expect(screen.getByRole('tab', { name: '好友' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '好友知识' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '我的分享' })).toBeInTheDocument();
    // /app/knowledge legacy links land on the 我的分享 tab content.
    expect(await screen.findByText('我的知识卡片')).toBeInTheDocument();
  });

  it('friends page defaults to the friend manager tab', async () => {
    renderWithClient(<FriendsPage />);
    expect(await screen.findByText('添加好友（手机号）')).toBeInTheDocument();
  });
});
