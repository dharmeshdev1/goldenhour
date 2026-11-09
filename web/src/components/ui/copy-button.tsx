"use client";

import { useState } from "react";

export function CopyButton({ text, label = "Copy", className = "" }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      window.setTimeout(() => setDone(false), 1400);
    } catch {
      setDone(false);
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] text-ink-3 transition-colors hover:bg-[rgb(255_236_210/0.06)] hover:text-ink ${className}`}
      aria-label={done ? "Copied" : `${label}: ${text.length > 80 ? "snippet" : text}`}
    >
      <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4">
        {done ? <path d="M3 8.5l3 3 7-7" strokeLinecap="round" strokeLinejoin="round" /> : <><rect x="5" y="5" width="8.5" height="8.5" rx="1.6" /><path d="M10.5 5V3.6c0-.6-.5-1.1-1.1-1.1H3.6c-.6 0-1.1.5-1.1 1.1v5.8c0 .6.5 1.1 1.1 1.1H5" /></>}
      </svg>
      <span aria-hidden="true">{done ? "Copied" : label}</span>
    </button>
  );
}
