import { chainColor, chainName } from "@/lib/chains";

export function ChainDot({ chain, size = 8 }: { chain: string; size?: number }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block shrink-0 rounded-full"
      style={{ width: size, height: size, background: chainColor(chain), boxShadow: `0 0 0 3px color-mix(in oklab, ${chainColor(chain)} 18%, transparent)` }}
    />
  );
}

export function ChainChip({ chain, className = "" }: { chain: string; className?: string }) {
  return (
    <span className={`chip ${className}`}>
      <ChainDot chain={chain} size={7} />
      {chainName(chain)}
    </span>
  );
}

export function ChainName({ chain }: { chain: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <ChainDot chain={chain} />
      {chainName(chain)}
    </span>
  );
}
