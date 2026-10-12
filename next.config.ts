import type { NextConfig } from "next";

// Same-origin API proxy: the browser never needs to know the Python service URL.
const nextConfig: NextConfig = {
  // Backward compatibility for legacy long-running routes. The interactive
  // paper builder now uses an asynchronous start+poll workflow instead.
  experimental: { proxyTimeout: 310_000 },
  async rewrites() {
    const origin = process.env.QUESTGEN_API_ORIGIN || "http://127.0.0.1:8000";
    return [{ source: "/api/workspace/:path*", destination: `${origin}/api/:path*` }];
  },
};

export default nextConfig;
