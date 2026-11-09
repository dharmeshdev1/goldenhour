export function LogoMark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="gh-sun" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffd98a" />
          <stop offset="1" stopColor="#e59a22" />
        </linearGradient>
      </defs>
      <path d="M7 19a9 9 0 0 1 18 0z" fill="url(#gh-sun)" />
      <path d="M3.5 19h25" stroke="#f2b33d" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M9 22.5h14M12.5 25.5h7" stroke="#f2b33d" strokeOpacity=".45" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark />
      <span className="display text-[23px] leading-none tracking-[-0.01em] text-ink">Goldenhour</span>
    </span>
  );
}
