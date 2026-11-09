import Link from "next/link";
import type { ReactNode } from "react";
import { safeHref } from "@/components/ui/external";
import { SourceRef } from "@/components/ui/source-ref";

/**
 * Renders the small inline syntax of the method content as React elements, never as HTML:
 * **bold**, `code`, [label](href) and {src:id}.
 */
const TOKEN = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\{src:[a-z0-9-]+\})/g;

export function RichText({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(TOKEN)) {
    const at = m.index ?? 0;
    if (at > last) parts.push(text.slice(last, at));
    const t = m[0];
    const key = `t${i++}`;
    if (t.startsWith("**")) parts.push(<strong key={key} className="font-semibold text-ink">{t.slice(2, -2)}</strong>);
    else if (t.startsWith("`")) parts.push(<code key={key} className="mono rounded bg-bg-3 px-1.5 py-0.5 text-[0.88em] text-gold-2">{t.slice(1, -1)}</code>);
    else if (t.startsWith("{src:")) parts.push(<SourceRef key={key} id={t.slice(5, -1)} />);
    else {
      const label = t.slice(1, t.indexOf("]"));
      const href = t.slice(t.indexOf("(") + 1, -1);
      if (href.startsWith("/") && !href.startsWith("//")) {
        parts.push(
          <Link key={key} href={href} className="text-gold-2 hover:text-gold">
            {label}
          </Link>,
        );
      } else {
        const safe = safeHref(href);
        parts.push(
          safe ? (
            <a key={key} href={safe} target="_blank" rel="noopener noreferrer" className="text-gold-2 hover:text-gold">
              {label}
            </a>
          ) : (
            label
          ),
        );
      }
    }
    last = at + t.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}
