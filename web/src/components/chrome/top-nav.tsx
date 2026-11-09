"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { FreshnessPill } from "@/components/chrome/freshness";
import { Wordmark } from "@/components/chrome/logo";
import { isCurrent, NAV } from "@/components/chrome/nav";
import { REPO_URL } from "@/lib/api";

function GitHubIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export function TopNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const panelId = useId();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header
      className={`sticky top-0 z-50 border-b transition-colors duration-300 ${
        scrolled || open ? "border-line bg-bg/85 backdrop-blur-xl" : "border-transparent bg-transparent"
      }`}
    >
      <div className="shell flex h-16 items-center gap-6">
        <Link href="/" className="shrink-0 no-underline" aria-label="Goldenhour, war room">
          <Wordmark />
        </Link>
        <nav aria-label="Main" className="hidden flex-1 lg:block">
          <ul className="flex items-center gap-1">
            {NAV.map((item) => {
              const current = isCurrent(pathname, item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={current ? "page" : undefined}
                    className={`relative inline-flex h-9 items-center rounded-full px-3 text-[13.5px] no-underline transition-colors ${
                      current ? "bg-[rgb(255_236_210/0.07)] text-ink" : "text-ink-3 hover:text-ink"
                    }`}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden sm:inline-flex">
            <FreshnessPill />
          </span>
          <a
            href={REPO_URL}
            className="inline-flex size-9 items-center justify-center rounded-full text-ink-3 transition-colors hover:text-ink"
            aria-label="Goldenhour on GitHub"
          >
            <GitHubIcon />
          </a>
          <button
            type="button"
            className="btn btn-ghost btn-small lg:hidden"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((v) => !v)}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
              {open ? (
                <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              ) : (
                <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              )}
            </svg>
            Menu
          </button>
        </div>
      </div>
      <div id={panelId} hidden={!open} className="border-t border-line bg-bg/95 backdrop-blur-xl lg:hidden">
        <nav aria-label="Main" className="shell py-4">
          <ul className="grid grid-cols-2 gap-1">
            {NAV.map((item) => {
              const current = isCurrent(pathname, item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={current ? "page" : undefined}
                    onClick={() => setOpen(false)}
                    className={`flex h-11 items-center rounded-xl px-3 text-[15px] no-underline ${
                      current ? "bg-[rgb(255_236_210/0.07)] text-ink" : "text-ink-2"
                    }`}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
          <div className="mt-4 sm:hidden">
            <FreshnessPill />
          </div>
        </nav>
      </div>
    </header>
  );
}
