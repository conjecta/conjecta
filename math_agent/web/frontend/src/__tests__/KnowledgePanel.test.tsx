// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { KnowledgePanel } from '../components/KnowledgePanel';
import * as queries from '../api/queries';
import { useUiStore } from '../store/ui';

vi.mock('../api/queries', () => ({
  useKnowledge: vi.fn(),
  useMaterials: vi.fn(),
  useKnowledgeGraph: vi.fn(),
  useTranslateKnowledge: vi.fn(),
  useUpdateKnowledgeItem: vi.fn(),
  useDeleteKnowledgeItem: vi.fn(),
}));

describe('KnowledgePanel', () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    useUiStore.setState({ selectedKnowledgeTab: 'knowledge' });
    vi.mocked(queries.useKnowledge).mockReturnValue({
      data: { facts: [], intuitions: [], tricks: [], ok: true, source: 'jsonl' },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useKnowledge>);
    vi.mocked(queries.useMaterials).mockReturnValue({
      data: { ok: true, materials: [], source: 'jsonl' },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useMaterials>);
    vi.mocked(queries.useKnowledgeGraph).mockReturnValue({
      data: { ok: true, project_id: 'default', nodes: [], edges: [], source: 'jsonl' },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useKnowledgeGraph>);
    vi.mocked(queries.useTranslateKnowledge).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof queries.useTranslateKnowledge>);
    vi.mocked(queries.useUpdateKnowledgeItem).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof queries.useUpdateKnowledgeItem>);
    vi.mocked(queries.useDeleteKnowledgeItem).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof queries.useDeleteKnowledgeItem>);
  });

  it('renders the merged knowledge tab with facts, intuitions and tricks together', async () => {
    vi.mocked(queries.useKnowledge).mockReturnValue({
      data: {
        facts: [{ id: 'fact-1', statement: 'A verified fact about primes.' }],
        intuitions: [{ id: 'int-1', title: 'Try small cases first', body: 'Often reveals structure.' }],
        tricks: [{ id: 'trick-1', title: 'Completing the square', body: 'Classic move.' }],
        ok: true,
        source: 'jsonl',
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useKnowledge>);

    render(<KnowledgePanel />);
    expect(screen.getByRole('tab', { name: '知识' })).toBeInTheDocument();
    expect(screen.getByText(/A verified fact about primes/)).toBeInTheDocument();
    expect(screen.getByText(/Try small cases first/)).toBeInTheDocument();
    expect(screen.getByText(/Completing the square/)).toBeInTheDocument();
    // Types are distinguished by badges, not section headings.
    expect(screen.getByText('Verified fact')).toBeInTheDocument();
    expect(screen.getByText('Intuition')).toBeInTheDocument();
    expect(screen.getByText('Technique')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: '材料' }));
    expect(screen.getByText(/还没有原始材料/)).toBeInTheDocument();
  });

  it('renders long facts as scannable knowledge articles with metadata', () => {
    vi.mocked(queries.useKnowledge).mockReturnValue({
      data: {
        facts: [
          {
            id: 'fact-1',
            statement:
              'Convex concentration for simple random tensors (Theorem 1.3): If f : (R^{n^d}, ||·||_2) → R is convex and Lipschitz, then f(X) concentrates with Gaussian tails depending on d and the Lipschitz norm.',
            source: 'Talagrand notes',
            confidence: 0.91,
          },
        ],
        intuitions: [],
        tricks: [],
        ok: true,
        source: 'jsonl',
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useKnowledge>);

    render(<KnowledgePanel />);
    expect(
      screen.getByRole('button', { name: /Convex concentration for simple random tensors/i })
    ).toBeInTheDocument();
    expect(screen.getByText(/Talagrand notes/i)).toBeInTheDocument();
    expect(screen.getByText(/91%/i)).toBeInTheDocument();
  });

  it('lets readers switch a saved English fact between original and Chinese', async () => {
    vi.mocked(queries.useKnowledge).mockReturnValue({
      data: {
        facts: [{
          id: 'fact-bi',
          statement: 'Every finite subgroup of a field is cyclic.',
          statement_zh: '域的每个有限子群都是循环群。',
        }],
        intuitions: [],
        tricks: [],
        ok: true,
        source: 'jsonl',
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useKnowledge>);

    render(<KnowledgePanel />);
    expect(screen.getByText('Every finite subgroup of a field is cyclic.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '中文' }));
    expect(screen.getByText('域的每个有限子群都是循环群。')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '原文' }));
    expect(screen.getByText('Every finite subgroup of a field is cyclic.')).toBeInTheDocument();
  });

  it('marks long source URLs as wrapping metadata chips', () => {
    const sourceUrl =
      'https://www.math.uci.edu/~rvershyn/papers/concentration-of-random-tensors-and-related-results-with-a-long-file-name.pdf';
    vi.mocked(queries.useKnowledge).mockReturnValue({
      data: {
        facts: [
          {
            id: 'fact-url',
            statement: 'Tensor concentration: A reusable concentration fact.',
            source: sourceUrl,
          },
        ],
        intuitions: [],
        tricks: [],
        ok: true,
        source: 'jsonl',
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useKnowledge>);

    render(<KnowledgePanel />);
    const urlChip = screen.getByText(sourceUrl);
    expect(urlChip).toHaveClass('kb-url-chip');
    expect(urlChip).toHaveAttribute('title', sourceUrl);
  });

  it('renders materials and sources from real data hooks', async () => {
    vi.mocked(queries.useMaterials).mockReturnValue({
      data: {
        ok: true,
        materials: [
          {
            id: 'mat-1',
            label: 'Talagrand notes',
            text: 'Convex concentration source excerpt.',
            source: 'https://example.com/talagrand',
          },
        ],
        source: 'jsonl',
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useMaterials>);
    vi.mocked(queries.useKnowledgeGraph).mockReturnValue({
      data: {
        ok: true,
        project_id: 'default',
        nodes: [{ id: 'source-1', kind: 'source', label: 'Talagrand', status: 'reference' }],
        edges: [],
        source: 'jsonl',
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useKnowledgeGraph>);

    render(<KnowledgePanel />);
    await userEvent.click(screen.getByRole('tab', { name: '材料' }));
    expect(screen.getByText(/Talagrand notes/i)).toBeInTheDocument();
    // Sources live inside the 材料 tab behind a small 来源 section heading.
    expect(screen.getByText('来源')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Talagrand' })).toBeInTheDocument();
  });

  it('hides internal pipeline tags from the sources section', async () => {
    vi.mocked(queries.useKnowledgeGraph).mockReturnValue({
      data: {
        ok: true,
        project_id: 'default',
        nodes: [
          { id: 'source-1', kind: 'source', label: 'memory_consolidation', status: 'reference' },
          { id: 'source-2', kind: 'source', label: 'knowledge_evaluator', status: 'reference' },
          {
            id: 'source-3',
            kind: 'source',
            label: 'https://www.math.uci.edu/~rvershyn/papers/concentration.pdf',
            status: 'reference',
          },
        ],
        edges: [],
        source: 'jsonl',
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useKnowledgeGraph>);

    render(<KnowledgePanel />);
    await userEvent.click(screen.getByRole('tab', { name: '材料' }));

    expect(screen.queryByText('memory_consolidation')).not.toBeInTheDocument();
    expect(screen.queryByText('knowledge_evaluator')).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'www.math.uci.edu/…/concentration.pdf' }),
    ).toBeInTheDocument();

    // Clicking the source card opens a detail dialog with the full URL link.
    await userEvent.click(screen.getByRole('button', { name: 'www.math.uci.edu/…/concentration.pdf' }));
    expect(
      screen.getByRole('link', {
        name: 'www.math.uci.edu/…/concentration.pdf',
      }),
    ).toHaveAttribute('href', 'https://www.math.uci.edu/~rvershyn/papers/concentration.pdf');
  });

  it('lets users edit and delete a fact from the detail dialog', async () => {
    const updateAsync = vi.fn().mockResolvedValue({ ok: true, item: { id: 'fact-1', statement: 'Updated' } });
    const deleteAsync = vi.fn().mockResolvedValue({ ok: true });
    vi.mocked(queries.useUpdateKnowledgeItem).mockReturnValue({
      mutateAsync: updateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof queries.useUpdateKnowledgeItem>);
    vi.mocked(queries.useDeleteKnowledgeItem).mockReturnValue({
      mutateAsync: deleteAsync,
      isPending: false,
    } as unknown as ReturnType<typeof queries.useDeleteKnowledgeItem>);
    vi.mocked(queries.useKnowledge).mockReturnValue({
      data: {
        facts: [{ id: 'fact-1', statement: 'Original fact statement for editing.', why: 'because' }],
        intuitions: [],
        tricks: [],
        ok: true,
        source: 'jsonl',
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useKnowledge>);

    render(<KnowledgePanel />);
    await userEvent.click(
      screen.getByRole('button', { name: /Original fact statement for editing/i }),
    );
    await userEvent.click(screen.getByRole('button', { name: /编辑/ }));
    const statement = screen.getByLabelText('陈述');
    await userEvent.clear(statement);
    await userEvent.type(statement, 'Revised fact statement.');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(updateAsync).toHaveBeenCalledWith({
      itemId: 'fact-1',
      kind: 'fact',
      fields: { statement: 'Revised fact statement.', why: 'because' },
    });

    await userEvent.click(
      screen.getByRole('button', { name: /Original fact statement for editing/i }),
    );
    await userEvent.click(screen.getByRole('button', { name: '删除知识' }));
    await userEvent.click(screen.getByRole('button', { name: '确认删除知识' }));
    expect(deleteAsync).toHaveBeenCalledWith({ itemId: 'fact-1', kind: 'fact' });
  });

  it('keeps all knowledge tabs visible in the narrow side panel', () => {
    render(<KnowledgePanel />);
    for (const label of ['知识', '材料']) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole('tablist')).toHaveClass('grid-cols-2');
  });
});
