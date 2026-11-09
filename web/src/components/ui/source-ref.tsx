"use client";

import { safeHref } from "@/components/ui/external";
import { useCase } from "@/lib/use-api";

/** A citation chip: the source id, linked to the source listed in case.json when there is one. */
export function SourceRef({ id, className = "" }: { id: string; className?: string }) {
  const kase = useCase();
  const source = kase.data?.sources.find((s) => s.id === id);
  const href = safeHref(source?.url);
  const cls = `inline-flex h-5 items-center rounded-full border border-line-2 px-1.5 align-[1px] font-mono text-[10.5px] tracking-wide text-ink-3 no-underline transition-colors ${className}`;
  if (!href) return <span className={cls}>{id}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" title={source?.title} className={`${cls} hover:border-gold-3 hover:text-gold-2`}>
      {id}
    </a>
  );
}
