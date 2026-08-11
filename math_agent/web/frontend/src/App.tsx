import { useEffect, useState } from 'react';
import { TopBar } from '@/components/TopBar';
import { SidePanel } from '@/components/SidePanel';
import { MainColumn } from '@/components/MainColumn';
import { TurnResultDrawer } from '@/components/TurnResultDrawer';
import { LoginGate } from '@/components/LoginGate';
import { useUiStore } from '@/store/ui';
import { AdminPage } from '@/components/AdminPage';
import { PublicGalleryPage } from '@/components/PublicGalleryPage';
import { CardDetailPage } from '@/components/CardDetailPage';
import { PublicCardView } from '@/components/PublicCardView';
import { FriendsPage } from '@/components/FriendsPage';
import { RefreshBanner } from '@/components/RefreshBanner';
import { useDeploymentVersion } from '@/hooks/useDeploymentVersion';
import { parseRoute } from '@/lib/router';

/** Append `#refresh-test` to force-show the corner toast for local UI checks. */
const showRefreshTest = typeof window !== 'undefined' && window.location.hash === '#refresh-test';

export default function App() {
  const updateAvailable = useDeploymentVersion();
  const [refreshDismissed, setRefreshDismissed] = useState(false);

  useEffect(() => {
    if (window.matchMedia('(max-width: 767px)').matches) {
      useUiStore.setState({ workbenchCollapsed: true });
    }
  }, []);

  const route = parseRoute(window.location.pathname);
  if (route.name === 'admin') {
    return (
      <LoginGate>
        <AdminPage />
      </LoginGate>
    );
  }
  if (route.name === 'friends') {
    return (
      <LoginGate>
        <FriendsPage />
      </LoginGate>
    );
  }
  if (route.name === 'knowledge-hub') {
    // Legacy link: "my cards" now lives under the friends hub's 我的分享 tab.
    return (
      <LoginGate>
        <FriendsPage initialTab="share" />
      </LoginGate>
    );
  }
  if (route.name === 'knowledge-gallery') {
    return (
      <LoginGate>
        <PublicGalleryPage />
      </LoginGate>
    );
  }
  if (route.name === 'knowledge-friends') {
    // Legacy link: friend cards now live under the friends hub's 好友知识 tab.
    return (
      <LoginGate>
        <FriendsPage initialTab="gallery" />
      </LoginGate>
    );
  }
  if (route.name === 'knowledge-card') {
    return (
      <LoginGate>
        <CardDetailPage cardId={route.cardId} />
      </LoginGate>
    );
  }
  if (route.name === 'knowledge-share') {
    return <PublicCardView cardId={route.cardId} />;
  }

  return (
    <LoginGate>
      <div className="flex h-[100dvh] w-full flex-col overflow-hidden">
        {(updateAvailable || showRefreshTest) && !refreshDismissed && (
          <RefreshBanner onClose={() => setRefreshDismissed(true)} />
        )}
        <TopBar />
        <div className="relative flex flex-1 overflow-hidden">
          <SidePanel />
          <MainColumn />
        </div>
        <TurnResultDrawer />
      </div>
    </LoginGate>
  );
}
