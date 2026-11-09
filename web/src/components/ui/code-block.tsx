"use client";

import { useId, useState, type ReactNode } from "react";
import { CopyButton } from "@/components/ui/copy-button";

/** Very small tokenizer for YAML and JSON: keys, strings, numbers and comments, rendered as text. */
export function highlight(code: string, lang: "yaml" | "json" | "plain"): ReactNode[] {
  if (lang === "plain") return [code];
  const out: ReactNode[] = [];
  code.split("\n").forEach((line, i) => {
    if (i > 0) out.push("\n");
    const comment = lang === "yaml" ? line.search(/(^|\s)#/) : -1;
    const body = comment >= 0 ? line.slice(0, comment) : line;
    const rest = comment >= 0 ? line.slice(comment) : "";
    const re = lang === "yaml" ? /^(\s*-?\s*)([A-Za-z0-9_.-]+)(:)(.*)$/ : /^(\s*)("[^"]+")(:)(.*)$/;
    const m = re.exec(body);
    const value = (text: string, key: string) => {
      const t = text.trim();
      if (!t) return text;
      const cls = /^-?\d+(\.\d+)?$/.test(t) || /^(true|false|null)$/.test(t) ? "tok-num" : /^["']/.test(t) ? "tok-str" : t.startsWith("[") || t.startsWith("{") ? "" : "tok-str";
      return cls ? (
        <span key={key} className={cls}>
          {text}
        </span>
      ) : (
        text
      );
    };
    if (m) {
      out.push(
        <span key={`${i}a`}>{m[1]}</span>,
        <span key={`${i}k`} className="tok-key">
          {m[2]}
        </span>,
        <span key={`${i}p`} className="tok-punct">
          {m[3]}
        </span>,
        value(m[4], `${i}v`),
      );
    } else {
      out.push(<span key={`${i}b`}>{body}</span>);
    }
    if (rest)
      out.push(
        <span key={`${i}c`} className="tok-com">
          {rest}
        </span>,
      );
  });
  return out;
}

export function CodeBlock({ code, lang = "plain", label, className = "", maxHeight }: { code: string; lang?: "yaml" | "json" | "plain"; label?: string; className?: string; maxHeight?: number }) {
  return (
    <div className={`relative overflow-hidden rounded-xl border border-line bg-[#0d0a08] ${className}`}>
      <div className="flex items-center justify-between border-b border-line px-4 py-2">
        <span className="mono text-[11px] text-ink-3">{label ?? lang}</span>
        <CopyButton text={code} />
      </div>
      <pre className="code overflow-auto p-4" style={maxHeight ? { maxHeight } : undefined}>
        <code>{highlight(code, lang)}</code>
      </pre>
    </div>
  );
}

export function CodeTabs({ tabs }: { tabs: { label: string; code: string; lang?: "yaml" | "json" | "plain" }[] }) {
  const [active, setActive] = useState(0);
  const id = useId();
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-[#0d0a08]">
      <div className="flex items-center justify-between border-b border-line pr-2">
        <div role="tablist" aria-label="Code samples" className="flex">
          {tabs.map((t, i) => (
            <button
              key={t.label}
              id={`${id}-tab-${i}`}
              type="button"
              role="tab"
              aria-selected={active === i}
              aria-controls={`${id}-panel`}
              tabIndex={active === i ? 0 : -1}
              onClick={() => setActive(i)}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight") setActive((active + 1) % tabs.length);
                if (e.key === "ArrowLeft") setActive((active - 1 + tabs.length) % tabs.length);
              }}
              className={`h-10 border-b-2 px-4 text-[12.5px] transition-colors ${active === i ? "border-gold text-ink" : "border-transparent text-ink-3 hover:text-ink"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <CopyButton text={tabs[active].code} />
      </div>
      <pre id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-tab-${active}`} className="code overflow-auto p-4">
        <code>{highlight(tabs[active].code, tabs[active].lang ?? "plain")}</code>
      </pre>
    </div>
  );
}
