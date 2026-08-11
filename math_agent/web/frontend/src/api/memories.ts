import { apiFetch } from './client';

export type UserMemoryStatus = 'candidate' | 'active' | 'snoozed';
export type UserMemoryKind = 'preference' | 'technique' | 'correction' | 'context';

export interface UserMemory {
  id: string;
  kind: UserMemoryKind;
  content: string;
  why: string;
  weight: number;
  status: UserMemoryStatus;
  scope: string;
  created_at: string;
  updated_at: string;
}

export interface UserProfileMemory {
  summary: string;
  version: number;
  generated_at: string;
}

export interface UserMemoryList {
  memories: UserMemory[];
  profile: UserProfileMemory | null;
}

export async function fetchUserMemories(): Promise<UserMemoryList> {
  const response = await apiFetch<{ ok: boolean } & UserMemoryList>('/api/me/memories');
  return { memories: response.memories, profile: response.profile };
}

export async function updateUserMemory(
  memoryId: string,
  changes: { status: UserMemoryStatus },
): Promise<UserMemory> {
  const response = await apiFetch<{ ok: boolean; memory: UserMemory }>(
    `/api/me/memories/${encodeURIComponent(memoryId)}`,
    { method: 'PATCH', body: JSON.stringify(changes) },
  );
  return response.memory;
}

export async function deleteUserMemory(memoryId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/api/me/memories/${encodeURIComponent(memoryId)}`, {
    method: 'DELETE',
  });
}

export async function clearUserProfile(): Promise<void> {
  await apiFetch<{ ok: boolean }>('/api/me/memories/profile', { method: 'DELETE' });
}
