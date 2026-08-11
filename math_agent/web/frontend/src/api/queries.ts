import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  apiFetch,
  buildQuery,
} from './client';
import type {
  Project,
  ReviewQueueItem,
  KnowledgeBundle,
  KnowledgeGraphResponse,
  LeanJob,
  Material,
  MathNewsResponse,
} from '@/types/api';

export const queryKeys = {
  projects: ['projects'] as const,
  project: (id: string, ownerUserId?: string | null) =>
    ['project', id, ownerUserId || ''] as const,
  reviewQueue: (id: string, ownerUserId?: string | null) =>
    ['reviewQueue', id, ownerUserId || ''] as const,
  knowledge: (id: string, ownerUserId?: string | null) =>
    ['knowledge', id, ownerUserId || ''] as const,
  knowledgeGraph: (id: string, ownerUserId?: string | null) =>
    ['knowledgeGraph', id, ownerUserId || ''] as const,
  materials: (id: string, ownerUserId?: string | null) =>
    ['materials', id, ownerUserId || ''] as const,
  leanJob: (id: string) => ['leanJob', id] as const,
  mathNews: ['mathNews'] as const,
};

function ownerQuery(ownerUserId?: string | null) {
  return ownerUserId ? { owner_user_id: ownerUserId } : {};
}

export function useProjects() {
  return useQuery({
    queryKey: queryKeys.projects,
    queryFn: () => apiFetch<{ projects: Project[] }>('/api/projects'),
  });
}

export function useProject(projectId: string, ownerUserId?: string | null) {
  return useQuery({
    queryKey: queryKeys.project(projectId, ownerUserId),
    queryFn: () =>
      apiFetch<Project>(
        `/api/projects/${encodeURIComponent(projectId)}${buildQuery(ownerQuery(ownerUserId))}`,
      ),
    enabled: Boolean(projectId),
  });
}

export function useStarProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, starred }: { projectId: string; starred: boolean }) =>
      apiFetch<Project>(`/api/projects/${encodeURIComponent(projectId)}/star`, {
        method: 'POST',
        body: JSON.stringify({ starred }),
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      queryClient.invalidateQueries({ queryKey: queryKeys.project(variables.projectId) });
    },
  });
}

export function useRenameProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      projectId,
      name,
      ownerUserId,
    }: {
      projectId: string;
      name: string;
      ownerUserId?: string | null;
    }) => {
      const trimmed = name.trim();
      if (!trimmed) {
        throw new Error('项目名称不能为空');
      }
      const q = buildQuery(ownerQuery(ownerUserId));
      const current = await apiFetch<{
        id: string;
        project?: Record<string, unknown>;
      }>(`/api/projects/${encodeURIComponent(projectId)}${q}`);
      const existing =
        current.project && typeof current.project === 'object'
          ? current.project
          : { id: projectId };
      return apiFetch<{ ok: boolean; id: string; project?: Record<string, unknown> }>(
        `/api/projects/${encodeURIComponent(projectId)}${q}`,
        {
          method: 'PUT',
          body: JSON.stringify({
            project: { ...existing, id: projectId, name: trimmed },
          }),
        },
      );
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      queryClient.invalidateQueries({
        queryKey: queryKeys.project(variables.projectId, variables.ownerUserId),
      });
    },
  });
}

export function useDeleteConversation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) =>
      apiFetch<{ ok: boolean; deleted: number }>(
        `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}`,
        { method: 'DELETE' },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects });
    },
  });
}

export function useReviewQueue(projectId: string) {
  return useQuery({
    queryKey: queryKeys.reviewQueue(projectId),
    queryFn: () =>
      apiFetch<{ items: ReviewQueueItem[] }>(
        `/api/projects/${encodeURIComponent(projectId)}/review-queue`
      ),
    enabled: Boolean(projectId),
  });
}

export function useKnowledge(projectId: string, ownerUserId?: string | null) {
  return useQuery({
    queryKey: queryKeys.knowledge(projectId, ownerUserId),
    queryFn: () =>
      apiFetch<KnowledgeBundle & { ok: boolean; source: string; error?: string }>(
        `/api/knowledge${buildQuery({ project_id: projectId, ...ownerQuery(ownerUserId) })}`
      ),
    enabled: Boolean(projectId),
  });
}

export function useTranslateKnowledge(projectId: string, ownerUserId?: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, kind }: { itemId: string; kind: 'fact' | 'intuition' | 'trick' }) =>
      apiFetch<{ ok: boolean; translation: Record<string, string>; cached: boolean; model?: string }>(
        '/api/knowledge/translate',
        {
          method: 'POST',
          body: JSON.stringify({
            project_id: projectId,
            item_id: itemId,
            kind,
            ...(ownerUserId ? { owner_user_id: ownerUserId } : {}),
          }),
        },
      ),
    onSuccess: (result, variables) => {
      queryClient.setQueryData<KnowledgeBundle & { ok: boolean; source: string }>(
        queryKeys.knowledge(projectId, ownerUserId),
        (current) => {
          if (!current) return current;
          if (variables.kind === 'fact') {
            return {
              ...current,
              facts: current.facts.map((item) =>
                item.id === variables.itemId ? { ...item, ...result.translation } : item,
              ),
            };
          }
          if (variables.kind === 'intuition') {
            return {
              ...current,
              intuitions: current.intuitions.map((item) =>
                item.id === variables.itemId ? { ...item, ...result.translation } : item,
              ),
            };
          }
          return {
            ...current,
            tricks: current.tricks.map((item) =>
              item.id === variables.itemId ? { ...item, ...result.translation } : item,
            ),
          };
        },
      );
    },
  });
}

export type KnowledgeApiKind = 'fact' | 'intuition' | 'trick';

export function useUpdateKnowledgeItem(projectId: string, ownerUserId?: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      itemId,
      kind,
      fields,
    }: {
      itemId: string;
      kind: KnowledgeApiKind;
      fields: Record<string, string>;
    }) =>
      apiFetch<{ ok: boolean; item: Record<string, unknown> }>(
        `/api/knowledge/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}${buildQuery({
          project_id: projectId,
          ...ownerQuery(ownerUserId),
        })}`,
        {
          method: 'PATCH',
          body: JSON.stringify(fields),
        },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.knowledge(projectId, ownerUserId) });
    },
  });
}

export function useDeleteKnowledgeItem(projectId: string, ownerUserId?: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, kind }: { itemId: string; kind: KnowledgeApiKind }) =>
      apiFetch<{ ok: boolean }>(
        `/api/knowledge/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}${buildQuery({
          project_id: projectId,
          ...ownerQuery(ownerUserId),
        })}`,
        { method: 'DELETE' },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.knowledge(projectId, ownerUserId) });
    },
  });
}

export function useMaterials(projectId: string) {
  return useQuery({
    queryKey: queryKeys.materials(projectId),
    queryFn: () =>
      apiFetch<{ ok: boolean; project_id: string; materials: Material[]; source: string; error?: string }>(
        `/api/materials${buildQuery({ project_id: projectId })}`
      ),
    enabled: Boolean(projectId),
  });
}

export function useKnowledgeGraph(projectId: string) {
  return useQuery({
    queryKey: queryKeys.knowledgeGraph(projectId),
    queryFn: () =>
      apiFetch<KnowledgeGraphResponse>(
        `/api/knowledge/graph${buildQuery({ project_id: projectId })}`
      ),
    enabled: Boolean(projectId),
  });
}

export function useExploreGraph(projectId: string, model?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (focus: string = '') =>
      apiFetch<KnowledgeGraphResponse>('/api/knowledge/graph/explore', {
        method: 'POST',
        body: JSON.stringify({ project_id: projectId, focus, model }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeGraph(projectId) });
    },
  });
}

export function useLeanJob(jobId: string) {
  return useQuery({
    queryKey: queryKeys.leanJob(jobId),
    queryFn: () => apiFetch<{ job: LeanJob }>(`/api/lean/jobs/${encodeURIComponent(jobId)}`),
    enabled: Boolean(jobId),
    refetchInterval: (q) => (q.state.data?.job.status === 'running' ? 2000 : false),
  });
}

export function useCreateLeanJob() {
  return useMutation({
    mutationFn: (code: string) =>
      apiFetch<{ job: LeanJob }>('/api/lean/jobs', {
        method: 'POST',
        body: JSON.stringify({ code }),
      }),
    onSuccess: () => {
      // no-op; consumers refetch via job id
    },
  });
}

export function useMathNews() {
  return useQuery({
    queryKey: queryKeys.mathNews,
    queryFn: () => apiFetch<MathNewsResponse>('/api/math-news'),
    staleTime: 10 * 60 * 1000,
  });
}
