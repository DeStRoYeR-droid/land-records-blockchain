import type { NextConfig } from "next";

/**
 * The browser never talks to the blockchain gateway directly: every call goes
 * to /gw/... on this site, and app/gw/[...path]/route.ts forwards it to the
 * gateway (LANDCHAIN_GATEWAY_URL). That keeps one origin, so the dashboards
 * also work when a teammate opens http://<your-laptop-ip>:3000.
 */
const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  // next dev would otherwise write AGENTS.md / CLAUDE.md into this folder
  agentRules: false,
  // Let teammates on the same Wi-Fi use the dev server (next dev blocks other origins by default).
  allowedDevOrigins: ["*.local", "192.168.*.*", "10.*.*.*", "172.*.*.*"],
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
