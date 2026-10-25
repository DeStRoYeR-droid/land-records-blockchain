import type { MetadataRoute } from "next";

import { COLORS, SITE_DESCRIPTION } from "@/lib/site";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "LandChain: Hanamkonda district land register",
    short_name: "LandChain",
    description: SITE_DESCRIPTION,
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: COLORS.paper,
    theme_color: COLORS.ink,
    lang: "en-IN",
    dir: "ltr",
    categories: ["government", "utilities", "productivity"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
    ],
    shortcuts: [
      { name: "Land register", url: "/register", description: "Look up a parcel by survey number" },
      { name: "Officers' desk", url: "/officials", description: "Files waiting for you" },
      { name: "Your land", url: "/citizen", description: "Your parcels, sales and reports" },
      { name: "Validator console", url: "/agent", description: "The AI validator, live" },
    ],
  };
}
