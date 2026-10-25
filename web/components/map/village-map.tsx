import { area as fmtArea, parcelPath, plural } from "@/lib/format";
import type { PublicParcel } from "@/lib/types";
import { buildVillageMap, pathOf } from "@/lib/villagemap";

/**
 * The landing page's hero: the register drawn as a village map. Every field
 * with a survey number is a parcel on the ledger (and a link to its record);
 * blank fields are land not on the digital register yet.
 */
export function VillageMap({ parcels, height, offline }: { parcels: PublicParcel[]; height: number | null; offline?: boolean }) {
  const map = buildVillageMap(parcels);
  const pending = parcels.filter((p) => p.status === "TRANSFER_PENDING").length;
  const villages = new Set(parcels.map((p) => p.village)).size;
  return (
    <figure className="village-map">
      <svg
        viewBox={`0 0 ${map.width} ${map.height}`}
        role="group"
        aria-label={
          parcels.length
            ? `Map of ${plural(parcels.length, "registered parcel")} in ${plural(villages, "village")}`
            : "Map of the register: no parcels registered yet"
        }
      >
        <defs>
          <pattern id="hatch-pending" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="7" height="7" className="hatch-bg" />
            <line x1="0" y1="0" x2="0" y2="7" className="hatch-line" />
          </pattern>
        </defs>
        {map.villages.map((v) => (
          <g key={v.name || "empty"}>
            {v.cells.map((c, i) => {
              const d = pathOf(c.poly);
              if (!c.parcel) return <path key={i} d={d} className="cell" />;
              const p = c.parcel;
              const pendingCell = p.status === "TRANSFER_PENDING";
              return (
                <a key={p.surveyNo} href={parcelPath(p.surveyNo)} aria-label={`Survey ${p.surveyNo}, ${p.village}, ${fmtArea(p.areaSqm)}${pendingCell ? ", transfer under way" : ""}`}>
                  <title>{`${p.surveyNo}, ${p.village}, ${fmtArea(p.areaSqm)}`}</title>
                  <path d={d} className={`cell parcel-cell ${pendingCell ? "cell-pending" : "cell-active"}`} />
                  {c.label ? (
                    <text x={c.centroid[0]} y={c.centroid[1] + 4} className="cell-label" textAnchor="middle">
                      {c.label}
                    </text>
                  ) : null}
                </a>
              );
            })}
            <path d={pathOf(v.poly)} className="village-edge" />
            {v.name ? (
              <text x={v.labelAt[0]} y={v.labelAt[1] + 5} className="village-label" textAnchor="middle">
                {v.name}
              </text>
            ) : null}
          </g>
        ))}
        {!parcels.length ? (
          <text x={map.width / 2} y={map.height / 2} className="village-label" textAnchor="middle">
            {offline ? "The register is offline" : "No parcels registered yet"}
          </text>
        ) : null}
      </svg>
      <figcaption>
        <ul className="map-legend" aria-label="Legend">
          <li>
            <i className="lg-active" aria-hidden="true" /> On the ledger
          </li>
          <li>
            <i className="lg-pending" aria-hidden="true" /> Transfer under way
          </li>
          <li>
            <i className="lg-blank" aria-hidden="true" /> Not yet on the register
          </li>
        </ul>
        <span>
          {offline
            ? "The ledger could not be reached just now."
            : height != null
              ? `Ledger at block ${height}: ${plural(parcels.length, "parcel")}${pending ? `, ${pending} with a transfer under way` : ""}. Field shapes are illustrative.`
              : "Field shapes are illustrative."}
        </span>
      </figcaption>
    </figure>
  );
}
