import type { ReactNode } from "react";

/** Validates a URL before it becomes a link: https or http only, otherwise plain text. */
export function safeHref(url: string | null | undefined): string | null {
  if (typeof url !== "string" || !url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export function ExternalIcon({ size = 11 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.3">
      <path d="M4.5 2.5h5v5M9.5 2.5 3 9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function ExternalLink({ href, children, className = "", label }: { href: string | null; children: ReactNode; className?: string; label?: string }) {
  const safe = safeHref(href);
  if (!safe) return <span className={className}>{children}</span>;
  return (
    <a href={safe} target="_blank" rel="noopener noreferrer" className={`inline-flex items-center gap-1 ${className}`} aria-label={label}>
      {children}
      <ExternalIcon />
    </a>
  );
}
