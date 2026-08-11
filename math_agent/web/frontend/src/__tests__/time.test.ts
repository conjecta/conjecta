import { describe, expect, it } from 'vitest';
import { formatDurationMs, formatElapsedSeconds } from '@/lib/time';

describe('formatElapsedSeconds', () => {
  it('formats seconds, minutes, and hours', () => {
    expect(formatElapsedSeconds(0)).toBe('0s');
    expect(formatElapsedSeconds(8.9)).toBe('8s');
    expect(formatElapsedSeconds(67)).toBe('1m 07s');
    expect(formatElapsedSeconds(3725)).toBe('1h 02m');
  });
});

describe('formatDurationMs', () => {
  it('formats sub-second, short, and long durations', () => {
    expect(formatDurationMs(320)).toBe('320ms');
    expect(formatDurationMs(1600)).toBe('1.6s');
    expect(formatDurationMs(125_000)).toBe('2m 05s');
  });

  it('returns an empty label for invalid input', () => {
    expect(formatDurationMs(Number.NaN)).toBe('');
    expect(formatDurationMs(-5)).toBe('');
  });
});
