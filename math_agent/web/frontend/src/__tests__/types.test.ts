import { describe, it, expect } from 'vitest';
import type { WsEvent, Project } from '../types';

describe('types', () => {
  it('WsEvent discriminates by type', () => {
    const e: WsEvent = { type: 'token', content: 'hello' };
    expect(e.type).toBe('token');
  });
  it('Project has required id', () => {
    const p: Project = { id: 'p1', name: 'P', doc: { id: 'p1' } };
    expect(p.id).toBe('p1');
  });
});
