"use client";

/**
 * The Sub-Registrar's "Register a parcel" form, with its boundary.
 *
 * The boundary comes from the village layout ("next free plot") or from the
 * surveyor's corner coordinates. Before anything is written, the gateway checks
 * it against the geospatial database: its area against the recorded extent, and
 * whether it overlaps a registered parcel. The map shows the proposal over the
 * parcels around it.
 */

import Link from "next/link";
import { useMemo, useState, type FormEvent } from "react";

import { LandMap } from "@/components/map/land-map";
import { api, errorMessage } from "@/lib/api";
import { useDesk } from "@/lib/desk";
import { area, number, parcelPath } from "@/lib/format";
import type { BoundaryCheck, GeoPolygon, ParcelCollection, ParcelFeature } from "@/lib/types";

type Mode = "auto" | "corners";

function parseCorners(text: string): GeoPolygon | string {
  const pts: [number, number][] = [];
  for (const raw of text.split(/\n|;/)) {
    const line = raw.trim();
    if (!line) continue;
    const nums = line.split(/[\s,]+/).map(Number);
    if (nums.length < 2 || nums.some((n) => !Number.isFinite(n))) return `“${line}” is not “longitude, latitude”`;
    let [lng, lat] = nums;
    if (lng < 40 && lat > 40) [lng, lat] = [lat, lng]; // accept "lat, lng" too (India: lat < 40 < lng)
    pts.push([lng, lat]);
  }
  if (pts.length < 3) return "Give at least three corners, one per line";
  if (pts[0][0] !== pts[pts.length - 1][0] || pts[0][1] !== pts[pts.length - 1][1]) pts.push(pts[0]);
  return { type: "Polygon", coordinates: [pts] };
}

export function RegisterParcel() {
  const { token } = useDesk();
  const [form, setForm] = useState({ surveyNo: "", village: "", district: "Hanamkonda", areaSqm: "", ownerDigilockerId: "" });
  const [mode, setMode] = useState<Mode>("auto");
  const [corners, setCorners] = useState("");
  const [check, setCheck] = useState<BoundaryCheck | null>(null);
  const [around, setAround] = useState<ParcelFeature[]>([]);
  const [dispute, setDispute] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<{ surveyNo: string; block?: number } | null>(null);

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm({ ...form, [k]: e.target.value });
    setCheck(null);
  };

  const preview = async (): Promise<BoundaryCheck | null> => {
    setProblem(null);
    if (!form.village.trim() || !Number(form.areaSqm)) {
      setProblem("Enter the village and the extent first.");
      return null;
    }
    setBusy(true);
    try {
      let res: BoundaryCheck;
      if (mode === "auto") {
        res = await api<BoundaryCheck>(`/api/geo/next-plot?village=${encodeURIComponent(form.village.trim())}&areaSqm=${Number(form.areaSqm)}`, { token });
      } else {
        const b = parseCorners(corners);
        if (typeof b === "string") {
          setProblem(b);
          return null;
        }
        res = await api<BoundaryCheck>("/api/geo/check-boundary", { token, method: "POST", body: { boundary: b, areaSqm: form.areaSqm } });
      }
      setCheck(res);
      const [lng, lat] = res.centroid;
      setAround((await api<ParcelCollection>(`/api/public/geo/near?lng=${lng}&lat=${lat}&radius=250&limit=40`)).features);
      return res;
    } catch (e) {
      setProblem(errorMessage(e));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const c = check ?? (await preview());
    if (!c) return;
    if (c.overlaps.length && !dispute) {
      setProblem("The boundary overlaps a registered parcel. Correct the corners, or record the dispute below.");
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      const r = await api<{ block?: number }>("/api/parcels", {
        token, method: "POST",
        body: { ...form, surveyNo: form.surveyNo.trim(), boundary: c.boundary, autoPlace: false, allowOverlap: dispute,
                note: dispute ? "boundary disputed" : undefined },
      });
      setDone({ surveyNo: form.surveyNo.trim(), block: r.block });
      setForm({ ...form, surveyNo: "", areaSqm: "", ownerDigilockerId: "" });
      setCheck(null);
      setDispute(false);
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const overlapIds = useMemo(() => (check?.overlaps ?? []).map((f) => f.id), [check]);
  const tooFar = check && !check.areaWithinTolerance;

  return (
    <form className="panel stack" onSubmit={submit}>
      <h2 style={{ fontSize: "var(--step-1)" }}>Register a parcel</h2>
      {done ? (
        <p className="callout" data-tone="ok" role="status">
          <Link href={parcelPath(done.surveyNo, "/officials/parcels")}>{done.surveyNo}</Link> is on the ledger
          {done.block !== undefined ? ` (block ${done.block})` : ""}, with its boundary.
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="rp-sy">Survey number</label>
        <input id="rp-sy" className="input input-mono" required value={form.surveyNo} onChange={set("surveyNo")} placeholder="SY-XXXX-12/3" />
      </div>
      <div className="form-row">
        <div className="field">
          <label htmlFor="rp-village">Village</label>
          <input id="rp-village" className="input" required value={form.village} onChange={set("village")} placeholder="Kazipet" />
        </div>
        <div className="field">
          <label htmlFor="rp-district">District</label>
          <input id="rp-district" className="input" required value={form.district} onChange={set("district")} />
        </div>
      </div>
      <div className="form-row">
        <div className="field">
          <label htmlFor="rp-area">Extent (sq.m)</label>
          <input id="rp-area" className="input" required inputMode="numeric" pattern="[0-9.]+" value={form.areaSqm} onChange={set("areaSqm")} />
        </div>
        <div className="field">
          <label htmlFor="rp-owner">Owner’s DigiLocker ID</label>
          <input id="rp-owner" className="input input-mono" required spellCheck={false} value={form.ownerDigilockerId} onChange={set("ownerDigilockerId")} />
        </div>
      </div>
      <fieldset className="stack-s" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend style={{ fontWeight: 700 }}>Boundary</legend>
        <div className="mode-tabs" role="group" aria-label="Where the boundary comes from">
          <button type="button" className="btn btn-small" aria-pressed={mode === "auto"} onClick={() => (setMode("auto"), setCheck(null))}>
            Next free plot in the village layout
          </button>
          <button type="button" className="btn btn-small" aria-pressed={mode === "corners"} onClick={() => (setMode("corners"), setCheck(null))}>
            Surveyor’s corners
          </button>
        </div>
        {mode === "corners" ? (
          <div className="field">
            <label htmlFor="rp-corners">Corners, one per line: longitude, latitude</label>
            <textarea id="rp-corners" className="input input-mono" rows={5} value={corners}
              onChange={(e) => (setCorners(e.target.value), setCheck(null))} placeholder={"79.48940, 17.96172\n79.48949, 17.96137\n79.49004, 17.96151\n79.48995, 17.96186"} />
            <span className="hint">WGS 84 decimal degrees, as a GPS or survey app gives them. The ring closes itself.</span>
          </div>
        ) : (
          <p className="small muted">
            The plot is laid out in the village’s grid next to the last registered plot, 40 m deep, wide enough for the extent.
          </p>
        )}
        <div className="cluster">
          <button type="button" className="btn" onClick={() => void preview()} disabled={busy}>
            {busy ? "Checking…" : "Check the boundary on the map"}
          </button>
        </div>
      </fieldset>

      {check ? (
        <div className="stack-s">
          <LandMap features={around.filter((f) => !overlapIds.includes(f.id)).concat(check.overlaps)} warn={overlapIds}
            proposal={check.boundary} proposalTone={check.overlaps.length || tooFar ? "bad" : "ok"} fitTo={overlapIds}
            height={300} label="The proposed boundary and the parcels around it" />
          <dl className="kv small">
            <dt>Encloses</dt>
            <dd>
              {area(check.boundaryAreaSqm)}, perimeter {number(Math.round(check.perimeterM))} m, {check.corners} corners
            </dd>
            <dt>Against the extent</dt>
            <dd>
              {check.areaDeviationPct === null ? "—" : `${check.areaDeviationPct}% apart`}{" "}
              {tooFar ? <strong className="error-text">(more than 5%: the ledger will refuse it)</strong> : "(within 5%)"}
            </dd>
            <dt>Touches</dt>
            <dd>{check.touching.length ? check.touching.join(", ") : "no registered parcel"}</dd>
          </dl>
          {check.overlaps.length ? (
            <div className="callout" data-tone="warn" role="alert">
              <p>
                Overlaps {check.overlaps.map((f) => `${f.id} by ${number(f.properties.overlapSqm ?? 0)} sq.m`).join(", ")}: the same
                ground would be recorded twice.
              </p>
              <label className="small">
                <input type="checkbox" checked={dispute} onChange={(e) => setDispute(e.target.checked)} /> Register it anyway and
                record a boundary dispute (the validator will flag every transfer of it)
              </label>
            </div>
          ) : null}
        </div>
      ) : null}
      {problem ? <p className="error-text" role="alert">{problem}</p> : null}
      <div className="cluster">
        <button className="btn btn-primary" type="submit" disabled={busy || Boolean(tooFar)}>
          Register the parcel
        </button>
      </div>
    </form>
  );
}
