import Link from "next/link";
import { LayerIcon } from "@/components/ui/layer-icon";
import { SectionHeader } from "@/components/ui/section";
import { SourceRef } from "@/components/ui/source-ref";
import { LAYERS } from "@/lib/layers";

export function LayersStrip() {
  return (
    <section className="shell mt-28" aria-labelledby="layers-title">
      <SectionHeader
        id="layers-title"
        eyebrow="Five layers"
        title="Each one breaks a link the Binance chain depended on"
        lead={<p>Use one or all five. Each layer is open source and runs on your infrastructure.</p>}
      />
      <ol className="relative grid gap-4 md:grid-cols-2 lg:grid-cols-5">
        <span aria-hidden="true" className="pointer-events-none absolute top-[38px] right-[10%] left-[10%] hidden h-px bg-[linear-gradient(90deg,transparent,rgba(242,179,61,0.45),transparent)] lg:block" />
        {LAYERS.map((layer) => (
          <li key={layer.id} className="card group relative flex flex-col p-5 transition-colors hover:border-[rgb(242_179_61/0.35)]">
            <div className="flex items-center justify-between">
              <span className="relative z-10 inline-flex size-10 items-center justify-center rounded-full border border-[rgb(242_179_61/0.35)] bg-bg-2 text-gold">
                <LayerIcon id={layer.id} />
              </span>
              <span className="num text-[12px] text-ink-4">0{layer.index}</span>
            </div>
            <h3 className="display mt-5 text-[30px] text-ink">{layer.name}</h3>
            <p className="mt-2 text-[14.5px] leading-snug text-ink">{layer.promise}</p>
            <div className="mt-5 flex-1 border-t border-line pt-4">
              <p className="eyebrow mb-2 text-[10px]">Where the chain broke</p>
              <p className="text-[13px] leading-relaxed text-ink-3">
                {layer.binance.text} <SourceRef id={layer.binance.source} className="relative z-10" />
              </p>
            </div>
            <Link href={layer.href} className="mt-5 inline-flex items-center gap-1.5 text-[13.5px] font-medium text-gold-2 no-underline after:absolute after:inset-0 after:content-[''] hover:text-gold">
              {layer.cta}
              <span aria-hidden="true" className="transition-transform group-hover:translate-x-0.5">
                →
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}
