/**
 * Conjecta mark: a QED triangle whose apex is a filled node and whose base
 * corners are open nodes — three points joined into one closed argument.
 * Sized by the caller via `className`.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 32 32" className={className} fill="none">
      <path
        d="M8 23.5 16 9l8 14.5M8 23.5h16"
        className="stroke-primary/45"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="8.5" r="3.1" className="fill-primary" />
      <circle cx="7.6" cy="23.6" r="2.6" className="fill-card stroke-primary" strokeWidth="1.5" />
      <circle cx="24.4" cy="23.6" r="2.6" className="fill-card stroke-primary" strokeWidth="1.5" />
    </svg>
  );
}
