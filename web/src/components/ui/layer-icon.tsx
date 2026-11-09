import type { LayerId } from "@/lib/layers";

/** Small line icons for the five layers. */
export function LayerIcon({ id, size = 20 }: { id: LayerId; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (id) {
    case "guard":
      return (
        <svg {...common}>
          <path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z" />
          <path d="M9 12l2 2 4-4" />
        </svg>
      );
    case "watch":
      return (
        <svg {...common}>
          <path d="M3 12h3l2-5 4 10 2-5h7" />
        </svg>
      );
    case "respond":
      return (
        <svg {...common}>
          <circle cx="12" cy="13" r="7" />
          <path d="M12 9.5V13l2.5 1.5M9.5 3h5" />
        </svg>
      );
    case "trace":
      return (
        <svg {...common}>
          <circle cx="5" cy="6" r="2" />
          <circle cx="19" cy="7" r="2" />
          <circle cx="12" cy="18" r="2" />
          <path d="M7 6.5l10 .5M6 8l5 8.3M17.8 8.6L13 16.4" />
        </svg>
      );
    case "screen":
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="6" />
          <path d="M20 20l-4.5-4.5M8.5 11h5" />
        </svg>
      );
  }
}
