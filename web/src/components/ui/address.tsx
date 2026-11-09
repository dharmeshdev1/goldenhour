import { CopyButton } from "@/components/ui/copy-button";
import { ExternalIcon } from "@/components/ui/external";
import { addressUrl, chainName } from "@/lib/chains";
import { shortAddress } from "@/lib/format";

/** An address: its label when it has one, otherwise the short form; the full address on hover. */
export function Address({
  chain,
  address,
  label,
  link = true,
  copy = false,
  className = "",
}: {
  chain: string;
  address: string;
  label?: string;
  link?: boolean;
  copy?: boolean;
  className?: string;
}) {
  const text = label?.trim() ? label.trim() : shortAddress(address);
  const url = link ? addressUrl(chain, address) : null;
  return (
    <span className={`inline-flex min-w-0 items-center gap-1 ${className}`}>
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          title={`${address} on ${chainName(chain)}`}
          className={`inline-flex min-w-0 items-center gap-1 no-underline hover:text-ink ${label?.trim() ? "" : "mono"}`}
        >
          <span className="truncate">{text}</span>
          <ExternalIcon size={10} />
        </a>
      ) : (
        <span title={address} className={`truncate ${label?.trim() ? "" : "mono"}`}>
          {text}
        </span>
      )}
      {copy ? <CopyButton text={address} label="Copy" /> : null}
    </span>
  );
}
