const AVATAR_COLORS = [
  'bg-sky-500',
  'bg-emerald-500',
  'bg-slate-500',
  'bg-amber-500',
  'bg-rose-500',
  'bg-cyan-500',
] as const;

export function avatarColor(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash + seed.charCodeAt(i)) % AVATAR_COLORS.length;
  }
  return AVATAR_COLORS[hash];
}

/** Last visible digits from masked phone, e.g. 138****5678 → 78 */
export function avatarInitials(phone: string): string {
  const digits = phone.replace(/\D/g, '');
  if (digits.length >= 2) return digits.slice(-2);
  return phone.slice(0, 1).toUpperCase() || '?';
}
