import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/site";

/** Public pages may be crawled; the signed-in dashboards and the API proxy may not. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/", "/register", "/verify", "/how-it-works", "/agent", "/citizen/register"],
        disallow: ["/officials", "/citizen", "/sign-in", "/gw/", "/api/"],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
