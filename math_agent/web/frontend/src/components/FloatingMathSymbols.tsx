const SYMBOLS: Array<{
  char: string;
  left: string;
  top: string;
  size: string;
  delay: string;
  duration: string;
}> = [
  { char: '∑', left: '8%', top: '18%', size: '2.2rem', delay: '0s', duration: '13s' },
  { char: '∫', left: '86%', top: '14%', size: '2.6rem', delay: '1.4s', duration: '15s' },
  { char: 'π', left: '14%', top: '68%', size: '1.8rem', delay: '2.2s', duration: '12s' },
  { char: '√', left: '78%', top: '72%', size: '2rem', delay: '0.8s', duration: '14s' },
  { char: '∞', left: '50%', top: '8%', size: '1.6rem', delay: '3s', duration: '16s' },
  { char: '∂', left: '30%', top: '12%', size: '1.5rem', delay: '2.6s', duration: '11s' },
  { char: 'α', left: '66%', top: '22%', size: '1.4rem', delay: '1s', duration: '13s' },
  { char: 'λ', left: '22%', top: '42%', size: '1.5rem', delay: '3.6s', duration: '15s' },
  { char: 'φ', left: '90%', top: '48%', size: '1.7rem', delay: '0.4s', duration: '12s' },
  { char: '∈', left: '6%', top: '86%', size: '1.4rem', delay: '2.9s', duration: '14s' },
  { char: '⇒', left: '72%', top: '88%', size: '1.6rem', delay: '1.8s', duration: '16s' },
  { char: '∇', left: '40%', top: '90%', size: '1.5rem', delay: '2.4s', duration: '13s' },
];

/** Slow-drifting math glyphs behind the empty-state hero. Purely decorative. */
export function FloatingMathSymbols() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 select-none overflow-hidden">
      {SYMBOLS.map((symbol) => (
        <span
          key={symbol.char}
          className="math-float absolute font-display italic text-primary/15 blur-[0.3px]"
          style={{
            left: symbol.left,
            top: symbol.top,
            fontSize: symbol.size,
            animationDelay: symbol.delay,
            animationDuration: symbol.duration,
          }}
        >
          {symbol.char}
        </span>
      ))}
    </div>
  );
}
