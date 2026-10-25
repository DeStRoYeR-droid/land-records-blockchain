/**
 * /og/parcel?surveyNo=SY-142-12/4A -- the share image for one parcel's page.
 * (A catch-all route cannot hold an opengraph-image file, so the parcel page
 * points its Open Graph and X image here explicitly.)
 */

import type { NextRequest } from "next/server";

import { area } from "@/lib/format";
import { gwMaybe } from "@/lib/server";
import { shareImage } from "@/lib/share-image";
import type { PublicParcel } from "@/lib/types";

export async function GET(req: NextRequest) {
  const surveyNo = (req.nextUrl.searchParams.get("surveyNo") || "").slice(0, 60);
  const [detail, all] = await Promise.all([
    surveyNo ? gwMaybe<{ parcel: PublicParcel }>(`/api/public/parcel?surveyNo=${encodeURIComponent(surveyNo)}`, 3000) : null,
    gwMaybe<{ parcels: PublicParcel[] }>("/api/public/parcels", 3000),
  ]);
  const p = detail?.parcel;
  const image = await shareImage({
    kicker: "On the LandChain register",
    title: p ? p.surveyNo : "A parcel on the register",
    subtitle: p
      ? `${p.village}, ${p.district} district. ${area(p.areaSqm)}, ${p.status === "ACTIVE" ? "registered" : "transfer under way"}.`
      : "Hanamkonda district land register.",
    parcels: all?.parcels?.length ? all.parcels : undefined,
    highlight: p?.surveyNo,
  });
  image.headers.set("Cache-Control", "public, max-age=300");
  return image;
}
