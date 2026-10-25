"use client";

/**
 * The public land register as a map: search by text, area and status, by the
 * map window ("search this area"), or around a point ("near here"). Every
 * query runs against the geospatial database (an OGC GeoPackage with an
 * R*Tree index) through the gateway's /api/public/geo endpoints.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ParcelBadge } from "@/components/ui/badges";
import { EmptyState } from "@/components/ui/states";
import { api, errorMessage, gwUrl } from "@/lib/api";
import { area, day, number, parcelPath, plural } from "@/lib/format";
import type { GeoStats, ParcelCollection, ParcelFeature } from "@/lib/types";

import { type BBox, LandMap } from "./land-map";

type Mode = "list" | "window" | "near";

export function RegisterExplorer({ initial, stats, initialQuery }: { initial: ParcelFeature[]; stats: GeoStats | null; initialQuery: string }) {
  const [q, setQ] = useState(initialQuery);
  const [status, setStatus] = useState("");
  const [minArea, setMinArea] = useState("");
  const [maxArea, setMaxArea] = useState("");
  const [mode, setMode] = useState<Mode>("list");
  const [radius, setRadius] = useState(500);
  const [point, setPoint] = useState<[number, number] | null>(null);
  const [features, setFeatures] = useState<ParcelFeature[]>(initial);
  const [context] = useState<ParcelFeature[]>(initial);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [searched, setSearched] = useState(initialQuery);
  const bbox = useRef<BBox | null>(null);
  const seq = useRef(0);

  const filters = useCallback(() => {
    const p = new URLSearchParams();
    if (q.trim()) p.set("q", q.trim());
    if (status) p.set("status", status);
    if (minArea) p.set("minArea", minArea);
    if (maxArea) p.set("maxArea", maxArea);
    return p;
  }, [q, status, minArea, maxArea]);

  const run = useCallback(async (how: Mode, at: [number, number] | null = point) => {
    const id = ++seq.current;
    setBusy(true);
    setProblem(null);
    try {
      let data: ParcelCollection;
      if (how === "near" && at) {
        data = await api<ParcelCollection>(`/api/public/geo/near?lng=${at[0]}&lat=${at[1]}&radius=${radius}&limit=60`);
        const f = filters();
        data.features = data.features.filter((x) => (!f.get("status") || x.properties.status === f.get("status")) &&
          (!minArea || x.properties.areaSqm >= Number(minArea)) && (!maxArea || x.properties.areaSqm <= Number(maxArea)) &&
          (!q.trim() || `${x.id} ${x.properties.village} ${x.properties.district}`.toLowerCase().includes(q.trim().toLowerCase())));
      } else {
        const p = filters();
        if (how === "window" && bbox.current) p.set("bbox", bbox.current.map((v) => v.toFixed(6)).join(","));
        data = await api<ParcelCollection>(`/api/public/geo/parcels?${p.toString()}`);
      }
      if (id !== seq.current) return;
      setFeatures(data.features);
      setSearched(q.trim());
    } catch (e) {
      if (id === seq.current) setProblem(errorMessage(e));
    } finally {
      if (id === seq.current) setBusy(false);
    }
  }, [filters, point, radius, q, minArea, maxArea]);

  // re-run when the radius changes in "near" mode
  useEffect(() => {
    if (mode === "near" && point) void run("near", point);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [radius]);

  const onPick = useCallback((lng: number, lat: number) => {
    if (mode !== "near") return;
    setPoint([lng, lat]);
    void run("near", [lng, lat]);
  }, [mode, run]);

  const onMoveEnd = useCallback((b: BBox) => {
    bbox.current = b;
    if (mode === "window") void run("window");
  }, [mode, run]);

  const picked = useMemo(() => features.find((f) => f.id === selected) ?? context.find((f) => f.id === selected) ?? null, [features, context, selected]);
  // in "this map area" and "near a point" every parcel stays on the map for context; the results are emphasised
  const drawn = useMemo(() => {
    if (mode === "list") return features;
    const ids = new Set(features.map((f) => f.id));
    return [...context.filter((f) => !ids.has(f.id)), ...features];
  }, [mode, features, context]);
  const emphasised = useMemo(() => (mode === "list" ? (selected ? [selected] : []) : [...features.map((f) => f.id), ...(selected ? [selected] : [])]),
    [mode, features, selected]);
  const active = Boolean(searched || status || minArea || maxArea || mode !== "list");

  const reset = () => {
    setQ(""); setStatus(""); setMinArea(""); setMaxArea(""); setMode("list"); setPoint(null); setSelected(null);
    void (async () => {
      setBusy(true);
      try {
        setFeatures((await api<ParcelCollection>("/api/public/geo/parcels")).features);
        setSearched("");
      } catch (e) {
        setProblem(errorMessage(e));
      } finally {
        setBusy(false);
      }
    })();
  };

  return (
    <div className="stack">
      <div className="explorer">
        <div className="stack-s">
          <LandMap
            features={drawn}
            highlight={emphasised}
            fitTo={mode === "list" ? "all" : "none"}
            point={mode === "near" ? point : null}
            radiusM={mode === "near" ? radius : null}
            height={480}
            label="Map of the parcels on the land register"
            onSelect={setSelected}
            onPick={onPick}
            onMoveEnd={onMoveEnd}
          />
          {mode === "near" && !point ? <p className="callout small">Click anywhere on the map to list the parcels around that spot.</p> : null}
        </div>

        <form
          className="explorer-tools panel"
          role="search"
          aria-label="Search the register"
          onSubmit={(e) => {
            e.preventDefault();
            void run(mode);
          }}
        >
          <fieldset>
            <legend>Find parcels</legend>
            <label className="visually-hidden" htmlFor="geo-q">Survey number, village or district</label>
            <input id="geo-q" className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Survey number, village or district" maxLength={80} autoComplete="off" />
            <div className="explorer-row">
              <label>
                Status
                <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
                  <option value="">Any</option>
                  <option value="ACTIVE">Registered</option>
                  <option value="TRANSFER_PENDING">Transfer under way</option>
                </select>
              </label>
              <label>
                Min sq.m
                <input className="input" inputMode="numeric" value={minArea} onChange={(e) => setMinArea(e.target.value.replace(/[^\d.]/g, ""))} />
              </label>
              <label>
                Max sq.m
                <input className="input" inputMode="numeric" value={maxArea} onChange={(e) => setMaxArea(e.target.value.replace(/[^\d.]/g, ""))} />
              </label>
            </div>
          </fieldset>
          <fieldset>
            <legend>Where</legend>
            <div className="mode-tabs" role="group" aria-label="Search area">
              {([["list", "Everywhere"], ["window", "This map area"], ["near", "Near a point"]] as [Mode, string][]).map(([m, text]) => (
                <button key={m} type="button" className="btn btn-small" aria-pressed={mode === m}
                  onClick={() => { setMode(m); if (m !== "near") void run(m); }}>
                  {text}
                </button>
              ))}
            </div>
            {mode === "window" ? <p className="small muted">The list follows the map: pan or zoom to search another area.</p> : null}
            {mode === "near" ? (
              <label className="small">
                Within {number(radius)} m
                <input type="range" min={100} max={3000} step={100} value={radius} onChange={(e) => setRadius(Number(e.target.value))} />
              </label>
            ) : null}
          </fieldset>
          <div className="cluster">
            <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Searching…" : "Search"}</button>
            {active ? <button className="btn btn-quiet" type="button" onClick={reset}>Clear</button> : null}
          </div>

          {picked ? (
            <div className="panel picked" aria-live="polite">
              <h3 className="survey-no" style={{ margin: 0 }}>{picked.id}</h3>
              <p className="small" style={{ margin: "0.25rem 0" }}>
                {picked.properties.village}, {picked.properties.district} · {area(picked.properties.areaSqm)} ·{" "}
                <ParcelBadge status={picked.properties.status} />
              </p>
              <p className="small muted" style={{ margin: 0 }}>
                {plural(picked.properties.owners, "owner")} on the ledger · owner {picked.properties.ownerInitials || "—"}
              </p>
              <Link className="btn btn-small" href={parcelPath(picked.id)} style={{ marginTop: "var(--s-2)" }}>
                Open the record and its chain of ownership
              </Link>
            </div>
          ) : null}

          {stats ? (
            <div className="geo-db" aria-label="About the geospatial database">
              <span>{stats.format}</span>
              <span>Index: {stats.spatialIndex}</span>
              <span>{plural(stats.parcels, "parcel")}, synced to block {stats.syncedToBlock ?? "—"}</span>
              <span>
                <a href={gwUrl("/api/public/geo/export.gpkg")}>Download .gpkg</a> · <a href={gwUrl("/api/public/geo/export.geojson")}>GeoJSON</a>
              </span>
            </div>
          ) : null}
        </form>
      </div>

      {problem ? (
        <div className="error-state" role="alert"><h2>The search did not run</h2><p>{problem}</p></div>
      ) : features.length === 0 ? (
        <EmptyState
          title={searched ? `No parcel matches “${searched}”` : mode === "near" ? "No parcels within this distance" : mode === "window" ? "No parcels in this part of the map" : "No parcels are registered yet"}
          action={active ? <button className="btn" type="button" onClick={reset}>Show every parcel</button> : null}
        >
          {active ? (
            <ul>
              <li>Survey numbers are written like SY-142-12/4A; try part of one, such as 12/4A.</li>
              <li>Search by village (Kazipet) or district (Hanamkonda), widen the area range, or zoom the map out.</li>
              <li>Names are not searchable here: the public register shows owners by initials only.</li>
            </ul>
          ) : (
            <p>Parcels appear here once the Sub-Registrar records them on the ledger.</p>
          )}
        </EmptyState>
      ) : (
        <div className="panel panel-flush">
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Parcels found</caption>
              <thead>
                <tr>
                  <th scope="col">Survey number</th>
                  <th scope="col">Village</th>
                  <th scope="col" className="right">Extent</th>
                  {mode === "near" ? <th scope="col" className="right">Distance</th> : null}
                  <th scope="col">Owner</th>
                  <th scope="col" className="right">Owners so far</th>
                  <th scope="col">Status</th>
                  <th scope="col">Last transfer</th>
                </tr>
              </thead>
              <tbody>
                {features.map((f) => (
                  <tr key={f.id} aria-selected={f.id === selected} onMouseEnter={() => setSelected(f.id)}>
                    <th scope="row">
                      <Link className="row-link survey-no" href={parcelPath(f.id)}>{f.id}</Link>
                    </th>
                    <td>{f.properties.village}</td>
                    <td className="right num">{area(f.properties.areaSqm)}</td>
                    {mode === "near" ? <td className="right num">{number(f.properties.distanceM ?? 0)} m</td> : null}
                    <td>{f.properties.ownerInitials || "—"}</td>
                    <td className="right num">{f.properties.owners}</td>
                    <td><ParcelBadge status={f.properties.status} /></td>
                    <td>{f.properties.lastTransferAt ? day(f.properties.lastTransferAt) : <span className="faint">None</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {features.length ? (
        <p className="faint small" role="status">
          {plural(features.length, "parcel")}
          {searched ? ` match “${searched}”` : ""}
          {mode === "near" && point ? ` within ${number(radius)} m of the point` : mode === "window" ? " in this map area" : ""}.
        </p>
      ) : null}
    </div>
  );
}
