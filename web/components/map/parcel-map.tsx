"use client";

import { useRouter } from "next/navigation";

import { parcelPath } from "@/lib/format";
import type { ParcelFeature } from "@/lib/types";

import { LandMap } from "./land-map";

/** One parcel and the parcels around it; clicking a neighbour opens its record. */
export function ParcelMap({ parcel, neighbours, warn = [], base = "/register", height = 360 }: {
  parcel: ParcelFeature;
  neighbours: ParcelFeature[];
  warn?: string[];
  base?: string;
  height?: number;
}) {
  const router = useRouter();
  return (
    <LandMap
      features={[...neighbours, parcel]}
      highlight={[parcel.id]}
      warn={warn}
      fitTo={[parcel.id, ...neighbours.map((n) => n.id)]}
      height={height}
      label={`Map of parcel ${parcel.id} and its neighbours`}
      onSelect={(sy) => (sy === parcel.id ? undefined : router.push(parcelPath(sy, base)))}
    />
  );
}
