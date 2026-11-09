import type { NextConfig } from "next";

// The public site lives under /goldenhour on GitHub Pages. A self-hosted build sets
// NEXT_PUBLIC_BASE_PATH to an empty string and serves the app from the root.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/goldenhour";
// Where the JSON API lives. Defaults to the files the pipeline publishes next to the site.
const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? `${basePath}/api/v1`;

const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  basePath,
  images: { unoptimized: true },
  reactStrictMode: true,
  poweredByHeader: false,
  devIndicators: false,
  // Do not write tool instruction files into the project when the dev server starts.
  agentRules: false,
  env: {
    GOLDENHOUR_BASE_PATH: basePath,
    GOLDENHOUR_API_BASE: apiBase,
  },
};

export default config;
