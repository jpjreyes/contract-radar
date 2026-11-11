import type { NextConfig } from "next";

const API = process.env.API_URL ?? "http://localhost:8071";

const nextConfig: NextConfig = {
  devIndicators: false,
  output: "standalone", // the container image runs .next/standalone/server.js
  // the browser talks to /api; Next forwards to the FastAPI service (no CORS, one origin)
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
