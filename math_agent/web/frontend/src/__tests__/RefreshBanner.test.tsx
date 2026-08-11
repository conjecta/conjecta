// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { RefreshBanner } from '../components/RefreshBanner';

describe('RefreshBanner', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders the refresh message and buttons', () => {
    render(<RefreshBanner onClose={() => {}} />);
    expect(screen.getByText(/系统已更新/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /刷新页面/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /关闭提示/ })).toBeInTheDocument();
  });

  it('calls the provided onRefresh handler', () => {
    const onRefresh = vi.fn();
    render(<RefreshBanner onRefresh={onRefresh} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /刷新页面/ }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('calls the provided onClose handler', () => {
    const onClose = vi.fn();
    render(<RefreshBanner onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: /关闭提示/ }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
