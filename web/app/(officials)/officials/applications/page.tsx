"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { StatusBadge, VerdictBadge } from "@/components/ui/badges";
import { ErrorState, NoResults } from "@/components/ui/states";
import { Loading, SkeletonTable } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import { useDesk } from "@/lib/desk";
import { rupees, STATUS_INFO } from "@/lib/format";
import type { Application } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function Inner() {
  const { token } = useDesk();
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState(params.get("status") ?? "ALL");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setDebounced(q.trim()), 250);
    return () => clearTimeout(id);
  }, [q]);
  const res = useApi<{ applications: Application[]; total: number }>(
    `/api/applications?q=${encodeURIComponent(debounced)}&status=${encodeURIComponent(status)}`,
    token,
  );
  const list = res.data?.applications ?? [];
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>All applications</h1>
          <p>Every transfer file on the ledger. Search by file number, survey number, a name or a status.</p>
        </div>
      </header>
      <form className="searchbar" role="search" aria-label="Search applications" onSubmit={(e) => e.preventDefault()}>
        <label htmlFor="q" className="visually-hidden">
          Search applications
        </label>
        <input id="q" className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="File number, survey number, seller or buyer" autoComplete="off" />
        <label htmlFor="status" className="visually-hidden">
          Status
        </label>
        <select id="status" className="select" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="ALL">Any status</option>
          {Object.entries(STATUS_INFO).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label}
            </option>
          ))}
        </select>
      </form>
      {res.error && !res.data ? (
        <ErrorState error={res.error} onRetry={res.reload} />
      ) : res.loading ? (
        <>
          <SkeletonTable rows={6} cols={6} />
          <Loading label="Loading applications" />
        </>
      ) : list.length === 0 ? (
        <NoResults
          what="applications"
          query={debounced || (status !== "ALL" ? STATUS_INFO[status as keyof typeof STATUS_INFO]?.label ?? status : "")}
          onClear={() => (setQ(""), setStatus("ALL"))}
          tips={res.data?.total ? ["Try a survey number such as the one on the deed.", "Names are matched anywhere in the name."] : ["No transfer has been filed yet."]}
        />
      ) : (
        <div className="panel panel-flush">
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Applications</caption>
              <thead>
                <tr>
                  <th scope="col">File</th>
                  <th scope="col">Parcel</th>
                  <th scope="col">Seller → Buyer</th>
                  <th scope="col" className="right">
                    Consideration
                  </th>
                  <th scope="col">Status</th>
                  <th scope="col">Validator</th>
                  <th scope="col">Updated</th>
                </tr>
              </thead>
              <tbody>
                {list.map((a) => (
                  <tr key={a.appId}>
                    <td>
                      <Link className="row-link mono" href={`/officials/applications/${a.appId}`}>
                        {a.appId}
                      </Link>
                    </td>
                    <td className="survey-no">{a.surveyNo}</td>
                    <td>
                      {a.sellerName} → {a.buyerName}
                    </td>
                    <td className="right num">{rupees(a.considerationINR)}</td>
                    <td>
                      <StatusBadge status={a.status} />
                    </td>
                    <td>
                      <VerdictBadge verdict={a.validations.at(-1)?.verdict} flags={a.validations.at(-1)?.flagCount} />
                    </td>
                    <td>
                      <TimeAgo value={a.updatedAt} />
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

export default function Applications() {
  return (
    <Suspense fallback={<SkeletonTable rows={6} cols={6} />}>
      <Inner />
    </Suspense>
  );
}
