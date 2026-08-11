import { apiFetch } from '@/api/client';

export interface FriendProfile {
  user_id: string;
  display_name: string;
  phone_masked: string;
  label: string;
}

export interface FriendRequest {
  id: string;
  status: string;
  created_at: string;
  other: FriendProfile;
}

export interface UserProfile {
  user_id: string;
  display_name: string;
  phone_masked: string;
  phone?: string;
}

export function getMyProfile() {
  return apiFetch<{ ok: boolean; profile: UserProfile }>('/api/me/profile');
}

export function updateMyProfile(displayName: string) {
  return apiFetch<{ ok: boolean; profile: UserProfile }>('/api/me/profile', {
    method: 'PATCH',
    body: JSON.stringify({ display_name: displayName }),
  });
}

export function listFriends() {
  return apiFetch<{ ok: boolean; friends: FriendProfile[] }>('/api/friends');
}

export function listFriendRequests() {
  return apiFetch<{ ok: boolean; incoming: FriendRequest[]; outgoing: FriendRequest[] }>(
    '/api/friends/requests',
  );
}

export function requestFriend(payload: { user_id?: string; phone?: string }) {
  return apiFetch<{ ok: boolean; friendship: Record<string, unknown>; created: boolean }>(
    '/api/friends/request',
    { method: 'POST', body: JSON.stringify(payload) },
  );
}

export function acceptFriendRequest(id: string) {
  return apiFetch(`/api/friends/requests/${encodeURIComponent(id)}/accept`, { method: 'POST' });
}

export function declineFriendRequest(id: string) {
  return apiFetch(`/api/friends/requests/${encodeURIComponent(id)}/decline`, { method: 'POST' });
}

export function unfriend(userId: string) {
  return apiFetch(`/api/friends/${encodeURIComponent(userId)}`, { method: 'DELETE' });
}

export function listProjectMembers(projectId: string, ownerUserId?: string | null) {
  const q = ownerUserId ? `?owner_user_id=${encodeURIComponent(ownerUserId)}` : '';
  return apiFetch<{
    ok: boolean;
    members: Array<{
      user_id: string;
      role: string;
      display_name: string;
      phone_masked: string;
      label: string;
    }>;
  }>(`/api/projects/${encodeURIComponent(projectId)}/members${q}`);
}

export function addProjectMember(
  projectId: string,
  member: { user_id?: string; phone?: string },
  ownerUserId?: string | null,
) {
  const q = ownerUserId ? `?owner_user_id=${encodeURIComponent(ownerUserId)}` : '';
  return apiFetch(`/api/projects/${encodeURIComponent(projectId)}/members${q}`, {
    method: 'POST',
    body: JSON.stringify(member),
  });
}

export function removeProjectMember(
  projectId: string,
  memberUserId: string,
  ownerUserId?: string | null,
) {
  const q = ownerUserId ? `?owner_user_id=${encodeURIComponent(ownerUserId)}` : '';
  return apiFetch(
    `/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(memberUserId)}${q}`,
    { method: 'DELETE' },
  );
}
