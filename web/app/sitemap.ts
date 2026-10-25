import type { MetadataRoute } from "next";

import { parcelPath } from "@/lib/format";
import { gwMaybe } from "@/lib/server";
import { SITE_URL } from "@/lib/site";
import type { PublicParcel } from "@/lib/types";

export const dynamic = "force-dynamic";

/** The public pages, plus one entry per parcel on the register (when the ledger answers). */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date();
  const pages: MetadataRoute.Sitemap = [
    { url: `${SITE_URL}/`, lastModified: now, changeFrequency: "hourly", priority: 1 },
    { url: `${SITE_URL}/register`, lastModified: now, changeFrequency: "hourly", priority: 0.9 },
    { url: `${SITE_URL}/verify`, changeFrequency: "monthly", priority: 0.7 },
    { url: `${SITE_URL}/how-it-works`, changeFrequency: "monthly", priority: 0.6 },
    { url: `${SITE_URL}/agent`, changeFrequency: "daily", priority: 0.6 },
    { url: `${SITE_URL}/citizen/register`, changeFrequency: "monthly", priority: 0.4 },
  ];
  const data = await gwMaybe<{ parcels: PublicParcel[] }>("/api/public/parcels", 3000);
  for (const p of data?.parcels ?? []) {
    pages.push({
      url: `${SITE_URL}${parcelPath(p.surveyNo)}`,
      lastModified: p.updatedAt ? new Date(p.updatedAt) : undefined,
      changeFrequency: "weekly",
      priority: 0.5,
    });
  }
  return pages;
}
