"use client";

import { useEffect, useState } from "react";

import { ParcelBadge } from "@/components/ui/badges";
import { ErrorState, NoResults } from "@/components/ui/states";
import { Loading, SkeletonTable } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import Link from "next/link";
import { useDesk } from "@/lib/desk";
import { area } from "@/lib/format";
import type { Parcel } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function Parcels() {
  const { token } = useDesk();
  const [q, setQ] = useState("");
  const [d, setD] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setD(q.trim()), 250);
    return () => clearTimeout(id);
  }, [q]);
  const res = useApi<{ parcels: Parcel[]; total: number }>(`/api/parcels?q=${encodeURIComponent(d)}`, token);
  const list = res.data?.parcels ?? [];
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>Parcels</h1>
          <p>Full records, with owners’ names. Open a parcel for its history of owners and transfers.</p>
        </div>
      </header>
      <form className="searchbar" role="search" aria-label="Search parcels" onSubmit={(e) => e.preventDefault()}>
        <label htmlFor="pq" className="visually-hidden">
          Search parcels
        </label>
        <input id="pq" className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Survey number, village, district or owner" autoComplete="off" />
      </form>
      {res.error && !res.data ? (
        <ErrorState error={res.error} onRetry={res.reload} />
      ) : res.loading ? (
        <>
          <SkeletonTable rows={6} cols={5} />
          <Loading label="Loading parcels" />
        </>
      ) : list.length === 0 ? (
        <NoResults what="parcels" query={d} onClear={() => setQ("")} tips={["Survey numbers look like SY-5D0E-12/4A.", "Owners are matched by their full name."]} />
      ) : (
        <div className="panel panel-flush">
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Parcels</caption>
              <thead>
                <tr>
                  <th scope="col">Survey no.</th>
                  <th scope="col">Village</th>
                  <th scope="col" className="right">
                    Extent
                  </th>
                  <th scope="col">Owner</th>
                  <th scope="col">Status</th>
                  <th scope="col">Updated</th>
                </tr>
              </thead>
              <tbody>
                {list.map((p) => (
                  <tr key={p.surveyNo}>
                    <td>
                      <Link className="row-link survey-no" href={`/officials/parcels/${p.surveyNo.split("/").map(encodeURIComponent).join("/")}`}>
                        {p.surveyNo}
                      </Link>
                    </td>
                    <td>
                      {p.village}, {p.district}
                    </td>
                    <td className="right num">{area(p.areaSqm)}</td>
                    <td>{p.ownerName}</td>
                    <td>
                      <ParcelBadge status={p.status} />
                    </td>
                    <td>
                      <TimeAgo value={p.updatedAt} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
