import { RegisterExplorer } from "@/components/map/register-explorer";
import { pageMetadata } from "@/lib/metadata";
import { gw, GatewayError } from "@/lib/server";
import type { GeoStats, ParcelCollection } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = { searchParams: Promise<{ q?: string }> };

export async function generateMetadata({ searchParams }: Props) {
  const { q } = await searchParams;
  return pageMetadata({
    title: q ? `Parcels matching “${q}”` : "Land register",
    description:
      "Every parcel on the LandChain ledger on a map of its surveyed boundary: search by survey number, village, area, map area or distance. Owners appear as initials only.",
    path: "/register",
    noindex: Boolean(q), // search results are not pages of their own
  });
}

export default async function RegisterPage({ searchParams }: Props) {
  const { q = "" } = await searchParams;
  const query = q.trim().slice(0, 80);
  let initial: ParcelCollection | null = null;
  let stats: GeoStats | null = null;
  let problem: string | null = null;
  try {
    [initial, stats] = await Promise.all([
      gw<ParcelCollection>(`/api/public/geo/parcels?q=${encodeURIComponent(query)}`),
      gw<GeoStats>("/api/public/geo/stats").catch(() => null),
    ]);
  } catch (e) {
    problem = e instanceof GatewayError ? e.message : "The register could not be read.";
  }

  return (
    <div className="page page-wide">
      <header className="page-head">
        <h1>Land register</h1>
        <p>
          Every parcel recorded on the ledger, on the map where its surveyed boundary lies. Search by survey number,
          village, area or status; search the part of the map you are looking at; or click a spot to find the parcels
          around it. Owners are shown by initials only.
        </p>
      </header>
      {problem ? (
        <div className="error-state" role="alert">
          <h2>The register is offline</h2>
          <p>{problem}</p>
          <p className="small">
            If you run LandChain, start the network with <code>./startup.sh</code> and reload this page.
          </p>
        </div>
      ) : (
        <RegisterExplorer initial={initial?.features ?? []} stats={stats} initialQuery={query} />
      )}
    </div>
  );
}
