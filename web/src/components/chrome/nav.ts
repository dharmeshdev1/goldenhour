export interface NavItem {
  href: string;
  label: string;
}

export const NAV: NavItem[] = [
  { href: "/", label: "War room" },
  { href: "/heist/", label: "Heist" },
  { href: "/golden-hour/", label: "Golden hour" },
  { href: "/trail/", label: "Trail" },
  { href: "/screen/", label: "Screen" },
  { href: "/guard/", label: "Guard" },
  { href: "/method/", label: "Method" },
  { href: "/api/", label: "API" },
];

/** usePathname() returns the path without the base path; compare without trailing slashes. */
export function isCurrent(pathname: string | null, href: string): boolean {
  const strip = (p: string) => (p.length > 1 ? p.replace(/\/+$/, "") : p);
  return strip(pathname ?? "/") === strip(href);
}
