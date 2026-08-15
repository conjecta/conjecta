// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { Composer, fileKind, pastedImageName } from '../components/Composer';

describe('Composer', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('names pasted images with a stable default', () => {
    expect(pastedImageName('image/png', 1)).toBe('pasted-image.png');
    expect(pastedImageName('image/jpeg', 2)).toBe('pasted-image-2.jpg');
  });

  it('sends problem on Enter', () => {
    const sendProblem = vi.fn();
    render(<Composer sendProblem={sendProblem} interrupt={vi.fn()} status="idle" />);
    const textarea = screen.getByPlaceholderText(/写下要证明/i);
    fireEvent.change(textarea, { target: { value: 'prove 1+1=2' } });
    fireEvent.keyDown(textarea, { key: 'Enter', code: 'Enter' });
    expect(sendProblem).toHaveBeenCalledWith(expect.objectContaining({ problem: 'prove 1+1=2' }));
  });

  it('sends problem when 求解 is clicked', () => {
    const sendProblem = vi.fn();
    render(<Composer sendProblem={sendProblem} interrupt={vi.fn()} status="idle" />);
    const textarea = screen.getByPlaceholderText(/写下要证明/i);
    fireEvent.change(textarea, { target: { value: 'solve x^2 = 4' } });
    fireEvent.click(screen.getByRole('button', { name: /求解/i }));
    expect(sendProblem).toHaveBeenCalledWith(
      expect.objectContaining({ problem: 'solve x^2 = 4', mode: 'auto' })
    );
    expect(sendProblem.mock.calls[0][0]).not.toHaveProperty('geeky');
  });

  it('has no research mode entry', () => {
    render(<Composer sendProblem={vi.fn()} interrupt={vi.fn()} status="idle" />);
    expect(screen.queryByRole('button', { name: '研究' })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: '求解模式' })).not.toBeInTheDocument();
  });

  it('shows 停止 button while busy and calls interrupt', () => {
    const interrupt = vi.fn();
    render(<Composer sendProblem={vi.fn()} interrupt={interrupt} status="streaming" />);
    expect(screen.getByRole('button', { name: /停止/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /停止/i }));
    expect(interrupt).toHaveBeenCalled();
  });

  it('does not claim busy messages are queued', () => {
    render(<Composer sendProblem={vi.fn()} interrupt={vi.fn()} status="streaming" />);
    expect(screen.queryByText(/they will run when this finishes/i)).not.toBeInTheDocument();
  });

  it('sends the default GPT-5.6 Sol model and never sends api_key', async () => {
    const sendProblem = vi.fn();
    render(<Composer sendProblem={sendProblem} interrupt={() => {}} status="idle" />);
    const box = screen.getByPlaceholderText(/写下要证明/i);
    fireEvent.change(box, { target: { value: '2+2?' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(sendProblem).toHaveBeenCalledTimes(1);
    const req = sendProblem.mock.calls[0][0];
    expect(req.problem).toBe('2+2?');
    expect(req.model).toBe('openai/gpt-5.6-sol');
    expect(req.api_key).toBeUndefined();
  });

  it('shows a fixed OpenAI GPT badge and always sends that model', async () => {
    const sendProblem = vi.fn();
    render(<Composer sendProblem={sendProblem} interrupt={() => {}} status="idle" />);

    expect(screen.getByLabelText('模型 OpenAI GPT-5.6 Sol')).toHaveTextContent('GPT-5.6 Sol');
    expect(screen.queryByRole('combobox', { name: '模型' })).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/写下要证明/i), {
      target: { value: '2+2?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /求解/i }));
    expect(sendProblem).toHaveBeenCalledWith(
      expect.objectContaining({ problem: '2+2?', model: 'openai/gpt-5.6-sol' }),
    );
  });

  it('classifies file kinds', () => {
    expect(fileKind('image/png')).toBe('image');
    expect(fileKind('image/jpeg')).toBe('image');
    expect(fileKind('image/webp')).toBe('image');
    expect(fileKind('image/gif')).toBe('image');
    expect(fileKind('image/svg+xml')).toBeNull();
    expect(fileKind('image/unknown')).toBeNull();
    expect(fileKind('application/pdf')).toBe('pdf');
    expect(fileKind('text/plain')).toBeNull();
  });

  it('rejects files beyond the eight-file cap before FileReader', () => {
    const read = vi.spyOn(FileReader.prototype, 'readAsDataURL');
    render(<Composer sendProblem={vi.fn()} interrupt={vi.fn()} status="idle" />);
    const files = Array.from(
      { length: 9 },
      (_, index) => new File(['x'], `proof-${index}.png`, { type: 'image/png' }),
    );
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, { target: { files } });

    expect(read).toHaveBeenCalledTimes(8);
    expect(screen.getByRole('alert')).toHaveTextContent(/8/);
  });

  it('rejects aggregate file bytes above eleven MiB before FileReader', () => {
    const read = vi.spyOn(FileReader.prototype, 'readAsDataURL');
    render(<Composer sendProblem={vi.fn()} interrupt={vi.fn()} status="idle" />);
    const first = new File(['a'], 'first.png', { type: 'image/png' });
    const second = new File(['b'], 'second.png', { type: 'image/png' });
    Object.defineProperty(first, 'size', { value: 10 * 1024 * 1024 });
    Object.defineProperty(second, 'size', { value: 2 * 1024 * 1024 });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [first, second] } });

    expect(read).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('alert')).toHaveTextContent(/11\s*MB/i);
  });

  it('rejects unsupported image MIME types before FileReader', () => {
    const read = vi.spyOn(FileReader.prototype, 'readAsDataURL');
    render(<Composer sendProblem={vi.fn()} interrupt={vi.fn()} status="idle" />);
    const svg = new File(['<svg/>'], 'diagram.svg', { type: 'image/svg+xml' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [svg] } });

    expect(read).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/PNG.*JPEG.*WebP.*GIF.*PDF/i);
    expect(input.accept).not.toContain('image/*');
  });

  it('does not resurrect a pending FileReader after removal', async () => {
    class ControlledFileReader {
      static instances: ControlledFileReader[] = [];
      result: string | ArrayBuffer | null = null;
      onload: ((event: ProgressEvent<FileReader>) => void) | null = null;
      onerror: ((event: ProgressEvent<FileReader>) => void) | null = null;
      onabort: ((event: ProgressEvent<FileReader>) => void) | null = null;

      constructor() {
        ControlledFileReader.instances.push(this);
      }

      readAsDataURL() {}

      abort() {
        this.onabort?.(new ProgressEvent('abort') as ProgressEvent<FileReader>);
      }

      complete() {
        this.result = 'data:image/png;base64,iVBORw0KGgo=';
        this.onload?.(new ProgressEvent('load') as ProgressEvent<FileReader>);
      }
    }
    vi.stubGlobal('FileReader', ControlledFileReader);
    render(<Composer sendProblem={vi.fn()} interrupt={vi.fn()} status="idle" />);
    const file = new File(['x'], 'pending.png', { type: 'image/png' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByText('pending.png')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /求解/i })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /remove pending\.png/i }));
    ControlledFileReader.instances[0].complete();

    await waitFor(() => expect(screen.queryByText('pending.png')).not.toBeInTheDocument());
  });

  it('attaches an image pasted into the textarea', async () => {
    const sendProblem = vi.fn();
    render(<Composer sendProblem={sendProblem} interrupt={vi.fn()} status="idle" />);

    const file = new File(['pasted-bytes'], 'clip.png', { type: 'image/png' });
    const textarea = screen.getByPlaceholderText(/写下要证明/i);
    fireEvent.paste(textarea, {
      clipboardData: {
        items: [
          {
            type: 'image/png',
            getAsFile: () => file,
          },
        ],
      },
    });

    await waitFor(() => {
      expect(screen.getByText('pasted-image.png')).toBeInTheDocument();
    });

    fireEvent.change(textarea, { target: { value: 'read this screenshot' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /求解/i })).not.toBeDisabled();
    });
    fireEvent.click(screen.getByRole('button', { name: /求解/i }));

    expect(sendProblem).toHaveBeenCalledWith(
      expect.objectContaining({
        problem: 'read this screenshot',
        files: [
          expect.objectContaining({
            kind: 'image',
            name: 'pasted-image.png',
          }),
        ],
      }),
    );
  });

  it('does not attach pasted images while busy', async () => {
    render(<Composer sendProblem={vi.fn()} interrupt={vi.fn()} status="streaming" />);

    const file = new File(['pasted-bytes'], 'clip.png', { type: 'image/png' });
    const textarea = screen.getByPlaceholderText(/写下要证明/i);
    fireEvent.paste(textarea, {
      clipboardData: {
        items: [
          {
            type: 'image/png',
            getAsFile: () => file,
          },
        ],
      },
    });

    expect(screen.queryByText(/pasted-image/i)).not.toBeInTheDocument();
  });

  it('allows sending with only an attached image and no text', async () => {
    const sendProblem = vi.fn();
    render(<Composer sendProblem={sendProblem} interrupt={vi.fn()} status="idle" />);

    const file = new File(['fake-image-bytes'], 'proof.png', { type: 'image/png' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText('proof.png')).toBeInTheDocument();
    });

    const sendBtn = screen.getByRole('button', { name: /求解/i });
    await waitFor(() => expect(sendBtn).not.toBeDisabled());
    fireEvent.click(sendBtn);

    expect(sendProblem).toHaveBeenCalledWith(
      expect.objectContaining({
        problem: '请根据附件中的题目进行求解。',
        files: [
          expect.objectContaining({
            kind: 'image',
            name: 'proof.png',
          }),
        ],
      }),
    );
  });

  it('attaches a file, shows a removable chip, and includes it in the request', async () => {
    const sendProblem = vi.fn();
    render(<Composer sendProblem={sendProblem} interrupt={vi.fn()} status="idle" />);

    const file = new File(['fake-image-bytes'], 'proof.png', { type: 'image/png' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toBeInTheDocument();

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText('proof.png')).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/写下要证明/i);
    fireEvent.change(textarea, { target: { value: 'what is in this image?' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /求解/i })).not.toBeDisabled();
    });
    fireEvent.click(screen.getByRole('button', { name: /求解/i }));

    expect(sendProblem).toHaveBeenCalledWith(
      expect.objectContaining({
        problem: 'what is in this image?',
        files: [
          expect.objectContaining({
            kind: 'image',
            name: 'proof.png',
          }),
        ],
      })
    );

    // chip clears after send
    await waitFor(() => {
      expect(screen.queryByText('proof.png')).not.toBeInTheDocument();
    });
  });

  it('removes an attached file when its chip remove button is clicked', async () => {
    render(<Composer sendProblem={vi.fn()} interrupt={vi.fn()} status="idle" />);

    const file = new File(['fake-pdf-bytes'], 'notes.pdf', { type: 'application/pdf' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText('notes.pdf')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /remove notes\.pdf/i }));

    expect(screen.queryByText('notes.pdf')).not.toBeInTheDocument();
  });

  it('removes only one chip when two attached files share the same name', async () => {
    render(<Composer sendProblem={vi.fn()} interrupt={vi.fn()} status="idle" />);

    const fileA = new File(['bytes-a'], 'scan.png', { type: 'image/png' });
    const fileB = new File(['bytes-b'], 'scan.png', { type: 'image/png' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [fileA] } });
    await waitFor(() => {
      expect(screen.getAllByText('scan.png')).toHaveLength(1);
    });

    fireEvent.change(input, { target: { files: [fileB] } });
    await waitFor(() => {
      expect(screen.getAllByText('scan.png')).toHaveLength(2);
    });

    const removeButtons = screen.getAllByRole('button', { name: /remove scan\.png/i });
    fireEvent.click(removeButtons[0]);

    // Only one of the two duplicate-named chips should be removed, not both.
    expect(screen.getAllByText('scan.png')).toHaveLength(1);
  });
});
