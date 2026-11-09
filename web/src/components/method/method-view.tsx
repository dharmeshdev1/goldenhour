"use client";

import { RichText } from "@/components/method/rich-text";
import { CodeBlock } from "@/components/ui/code-block";
import { safeHref } from "@/components/ui/external";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { METHOD } from "@/content/method";
import { useCase } from "@/lib/use-api";

function Sources() {
  const kase = useCase();
  if (kase.error && !kase.data) return <ErrorState error={kase.error} onRetry={kase.reload} what="The case file" />;
  if (!kase.data) return <Skeleton className="h-48 w-full" />;
  const c = kase.data;
  return (
    <>
      <ul className="divide-y divide-line rounded-2xl border border-line">
        {c.sources.map((s) => {
          const href = safeHref(s.url);
          return (
            <li key={s.id} className="grid gap-1 p-4 sm:grid-cols-[140px_1fr] sm:gap-4">
              <span className="mono text-[12px] text-gold-2">{s.id}</span>
              <span className="min-w-0 text-[14px]">
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer" className="text-ink hover:text-gold-2">
                    {s.title}
                  </a>
                ) : (
                  <span className="text-ink">{s.title}</span>
                )}
                {href ? <span className="mono mt-0.5 block truncate text-[11.5px] text-ink-3">{new URL(href).host}</span> : null}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-4 text-[13px] leading-relaxed text-ink-3">
        Reported totals: {c.reported.stolen_usd_source} for the amount stolen; {c.reported.frozen_usd_source} for the amount frozen or seized.
        Attribution: {c.attribution}.
      </p>
    </>
  );
}

export function MethodView() {
  return (
    <div className="shell grid gap-12 lg:grid-cols-[220px_minmax(0,1fr)]">
      <nav aria-label="On this page" className="hidden lg:block">
        <ol className="sticky top-24 space-y-1 border-l border-line">
          {[...METHOD.map((s) => ({ id: s.id, title: s.title })), { id: "sources", title: "Sources" }].map((s) => (
            <li key={s.id}>
              <a href={`#${s.id}`} className="-ml-px block border-l border-transparent py-1.5 pl-4 text-[13px] text-ink-3 no-underline hover:border-gold hover:text-ink">
                {s.title}
              </a>
            </li>
          ))}
        </ol>
      </nav>
      <div className="max-w-3xl min-w-0">
        {METHOD.map((section) => (
          <section key={section.id} id={section.id} className="mb-16 scroll-mt-24" aria-labelledby={`${section.id}-h`}>
            <h2 id={`${section.id}-h`} className="display mb-5 text-[32px] text-ink sm:text-[38px]">
              {section.title}
            </h2>
            <div className="space-y-4 text-[15.5px] leading-[1.75] text-ink-2">
              {section.blocks.map((b, i) => {
                if (b.type === "p")
                  return (
                    <p key={i}>
                      <RichText text={b.text} />
                    </p>
                  );
                if (b.type === "note")
                  return (
                    <p key={i} className="rounded-xl border border-[rgb(242_179_61/0.3)] bg-[rgb(242_179_61/0.06)] px-5 py-4 text-[16px] text-ink">
                      <RichText text={b.text} />
                    </p>
                  );
                if (b.type === "code") return <CodeBlock key={i} code={b.text} label={b.label} />;
                const List = b.ordered ? "ol" : "ul";
                return (
                  <List key={i} className={`space-y-2.5 pl-5 ${b.ordered ? "list-decimal" : "list-disc"} marker:text-gold-3`}>
                    {b.items.map((item, j) => (
                      <li key={j} className="pl-1">
                        <RichText text={item} />
                      </li>
                    ))}
                  </List>
                );
              })}
            </div>
          </section>
        ))}
        <section id="sources" className="scroll-mt-24" aria-labelledby="sources-h">
          <h2 id="sources-h" className="display mb-5 text-[32px] text-ink sm:text-[38px]">
            Sources
          </h2>
          <p className="mb-5 text-[15px] leading-relaxed text-ink-2">
            The public record this case file cites, as listed in <code className="mono rounded bg-bg-3 px-1.5 py-0.5 text-[0.88em] text-gold-2">case.json</code>.
          </p>
          <Sources />
        </section>
      </div>
    </div>
  );
}
