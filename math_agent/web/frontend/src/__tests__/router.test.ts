import { describe, it, expect } from 'vitest';
import { parseRoute } from '../lib/router';

describe('parseRoute', () => {
  it('no longer routes research pages', () => {
    expect(parseRoute('/app/research')).toEqual({ name: 'home' });
    expect(parseRoute('/app/research/sess-1')).toEqual({ name: 'home' });
    expect(parseRoute('/app/inbox')).toEqual({ name: 'home' });
    expect(parseRoute('/share/research/abc123')).toEqual({ name: 'home' });
  });
  it('parses friends and friend knowledge gallery', () => {
    expect(parseRoute('/app/friends')).toEqual({ name: 'friends' });
    expect(parseRoute('/app/knowledge/friends')).toEqual({ name: 'knowledge-friends' });
  });
  it('falls back to home and admin', () => {
    expect(parseRoute('/app')).toEqual({ name: 'home' });
    expect(parseRoute('/admin')).toEqual({ name: 'admin' });
    expect(parseRoute('/')).toEqual({ name: 'home' });
  });
});
