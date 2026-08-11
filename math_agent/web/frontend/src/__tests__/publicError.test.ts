import { describe, expect, it } from 'vitest';
import {
  CLOUD_STORAGE_REQUIRED_MESSAGE,
  QUOTA_EXCEEDED_MESSAGE,
  isPublicErrorMessage,
  isQuotaExceededMessage,
  messageFromStatusAndDetail,
  publicErrorMessage,
} from '@/lib/publicError';

describe('publicError', () => {
  it('maps DAILY_QUOTA_EXCEEDED to a bind-API-key message', () => {
    expect(messageFromStatusAndDetail(429, 'DAILY_QUOTA_EXCEEDED')).toBe(
      QUOTA_EXCEEDED_MESSAGE,
    );
    expect(isPublicErrorMessage(QUOTA_EXCEEDED_MESSAGE)).toBe(true);
  });

it('recognizes the quota-exceeded message helper', () => {
    expect(isQuotaExceededMessage(QUOTA_EXCEEDED_MESSAGE)).toBe(true);
    expect(isQuotaExceededMessage('当前请求较多，请稍后重试。')).toBe(false);
  });

  it('maps CLOUD_STORAGE_REQUIRED to a setup message', () => {
    expect(messageFromStatusAndDetail(503, 'CLOUD_STORAGE_REQUIRED')).toBe(
      CLOUD_STORAGE_REQUIRED_MESSAGE,
    );
    expect(isPublicErrorMessage(CLOUD_STORAGE_REQUIRED_MESSAGE)).toBe(true);
  });

  it('keeps generic 429 for other rate limits', () => {
    expect(messageFromStatusAndDetail(429, 'too many requests')).toBe(
      publicErrorMessage(429),
    );
  });
});
