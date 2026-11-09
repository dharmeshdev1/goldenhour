import type { ReactNode } from "react";

export function SectionHeader({
  eyebrow,
  title,
  lead,
  id,
  aside,
  as: Tag = "h2",
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  lead?: ReactNode;
  id?: string;
  aside?: ReactNode;
  as?: "h1" | "h2" | "h3";
}) {
  return (
    <div className="mb-8 flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
      <div className="max-w-3xl">
        {eyebrow ? <p className="eyebrow mb-3">{eyebrow}</p> : null}
        <Tag id={id} className={`display ${Tag === "h1" ? "text-[44px] sm:text-[60px]" : "text-[34px] sm:text-[44px]"} text-ink`}>
          {title}
        </Tag>
        {lead ? <div className="lead mt-4 max-w-2xl">{lead}</div> : null}
      </div>
      {aside ? <div className="shrink-0">{aside}</div> : null}
    </div>
  );
}

export function PageHero({ eyebrow, title, lead, children }: { eyebrow: ReactNode; title: ReactNode; lead?: ReactNode; children?: ReactNode }) {
  return (
    <div className="shell pt-14 pb-10 sm:pt-20">
      <p className="eyebrow mb-4 text-gold">{eyebrow}</p>
      <h1 className="display max-w-4xl text-[46px] text-ink sm:text-[68px]">{title}</h1>
      {lead ? <div className="lead mt-6 max-w-2xl text-[18px]">{lead}</div> : null}
      {children}
    </div>
  );
}
