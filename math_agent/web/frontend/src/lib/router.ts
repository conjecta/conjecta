export type Route =
  | { name: 'home' }
  | { name: 'admin' }
  | { name: 'knowledge-hub' }
  | { name: 'knowledge-gallery' }
  | { name: 'knowledge-friends' }
  | { name: 'knowledge-card'; cardId: string }
  | { name: 'knowledge-share'; cardId: string }
  | { name: 'friends' };

export function parseRoute(pathname: string): Route {
  const path = pathname.replace(/\/+$/, '') || '/';
  if (path.startsWith('/admin')) return { name: 'admin' };
  if (path === '/app/friends') return { name: 'friends' };
  if (path === '/app/knowledge') return { name: 'knowledge-hub' };
  if (path === '/app/knowledge/gallery') return { name: 'knowledge-gallery' };
  if (path === '/app/knowledge/friends') return { name: 'knowledge-friends' };
  const card = path.match(/^\/app\/knowledge\/card\/([^/]+)$/);
  if (card) return { name: 'knowledge-card', cardId: decodeURIComponent(card[1]) };
  const publicCard = path.match(/^\/share\/knowledge\/([^/]+)$/);
  if (publicCard) return { name: 'knowledge-share', cardId: decodeURIComponent(publicCard[1]) };
  return { name: 'home' };
}
