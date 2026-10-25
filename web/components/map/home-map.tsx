"use client";

import { useRouter } from "next/navigation";

import { parcelPath, plural } from "@/lib/format";
import type { ParcelFeature } from "@/lib/types";

import { LandMap } from "./land-map";

/** The home page's map: every registered boundary; a click opens the parcel's record. */
export function HomeMap({ features, height }: { features: ParcelFeature[]; height: number | null }) {
  const router = useRouter();
  return (
    <div className="stack-s">
      <LandMap features={features} height={400} label="Map of the parcels on the land register"
        onSelect={(sy) => router.push(parcelPath(sy))} />
      <p className="faint small">
        {plural(features.length, "surveyed boundary", "surveyed boundaries")} on the ledger
        {height !== null ? `, block ${height}` : ""}. Click a parcel to open its record.
      </p>
    </div>
  );
}
