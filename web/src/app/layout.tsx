import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Footer } from "@/components/chrome/footer";
import { TopNav } from "@/components/chrome/top-nav";
import { Providers } from "@/components/providers";
import { display, mono, sans } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Goldenhour: crypto theft response",
    template: "%s | Goldenhour",
  },
  description:
    "Open-source crypto theft response: guard the signer, detect the drain, file the freezes in the first hour, and follow the money across chains. Live case file for the Binance theft of 29 October 2026.",
  applicationName: "Goldenhour",
};

export const viewport: Viewport = {
  themeColor: "#0a0807",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} ${mono.variable}`}>
      <body>
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <Providers>
          <TopNav />
          <main id="main" tabIndex={-1} className="outline-none">
            {children}
          </main>
          <Footer />
        </Providers>
      </body>
    </html>
  );
}
