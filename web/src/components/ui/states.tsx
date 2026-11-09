"use client";

import { ApiError } from "@/lib/api";

export function Skeleton({ className = "", label = "Loading" }: { className?: string; label?: string }) {
  return <div className={`skeleton ${className}`} role="status" aria-label={label} />;
}

export function ErrorState({ error, onRetry, what }: { error: Error | null; onRetry?: () => void; what?: string }) {
  const missing = error instanceof ApiError && error.status === 404;
  return (
    <div className="card-quiet flex flex-col items-start gap-3 p-6" role="alert">
      <p className="text-[15px] text-ink">
        {missing ? `${what ?? "This data"} has not been published yet.` : `Could not load ${what ?? "this data"}.`}
      </p>
      <p className="text-[13px] text-ink-3">
        {missing
          ? "The pipeline publishes every few minutes. If this persists, the case may not be processed yet."
          : error?.message ?? "Network error."}
      </p>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="btn btn-ghost btn-small">
          Try again
        </button>
      ) : null}
    </div>
  );
}
