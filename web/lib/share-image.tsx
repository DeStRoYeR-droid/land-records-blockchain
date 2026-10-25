import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ImageResponse } from "next/og";

import { COLORS } from "./site";
import { buildVillageMap, pathOf, type MapParcel } from "./villagemap";

export const SHARE_SIZE = { width: 1200, height: 630 };

const SAMPLE: MapParcel[] = [
  ["SY-142-12/4A", "Kazipet", 1450],
  ["SY-142-42/1A", "Kazipet", 2400],
  ["SY-142-77/2B", "Kazipet", 1800],
  ["SY-210-3/1", "Madikonda", 3200],
  ["SY-210-8/2C", "Madikonda", 950],
  ["SY-305-19/1B", "Hasanparthy", 2750],
  ["SY-305-21/4", "Hasanparthy", 1200],
  ["SY-305-25/3A", "Hasanparthy", 1650],
].map(([surveyNo, village, areaSqm], i) => ({
  surveyNo: surveyNo as string,
  village: village as string,
  district: "Hanamkonda",
  areaSqm: areaSqm as number,
  status: i === 2 ? "TRANSFER_PENDING" : "ACTIVE",
}));

async function fonts() {
  const dir = join(process.cwd(), "assets", "og");
  const [regular, bold, heavy] = await Promise.all(
    ["atkinson-next-400.woff", "atkinson-next-700.woff", "atkinson-next-800.woff"].map((f) => readFile(join(dir, f))),
  );
  return [
    { name: "Atkinson", data: regular, weight: 400 as const, style: "normal" as const },
    { name: "Atkinson", data: bold, weight: 700 as const, style: "normal" as const },
    { name: "Atkinson", data: heavy, weight: 800 as const, style: "normal" as const },
  ];
}

/** The four-stone mark, drawn for the share image. */
function MarkSvg({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32">
      <path d="M5.5 12 18.5 5.5l8 11L12.5 26.5z" fill="none" stroke={COLORS.ink} strokeWidth="2.4" strokeLinejoin="round" />
      <circle cx="5.5" cy="12" r="3.1" fill={COLORS.reg} stroke="#fff" strokeWidth="1.3" />
      <circle cx="18.5" cy="5.5" r="3.1" fill={COLORS.field} stroke="#fff" strokeWidth="1.3" />
      <circle cx="26.5" cy="16.5" r="3.1" fill={COLORS.violet} stroke="#fff" strokeWidth="1.3" />
      <circle cx="12.5" cy="26.5" r="3.1" fill={COLORS.ink} stroke="#fff" strokeWidth="1.3" />
    </svg>
  );
}

/**
 * A 1200x630 card for Open Graph and X: the message on the left, a patch of
 * the village map on the right. `highlight` outlines one parcel (parcel pages).
 */
export async function shareImage({
  title,
  subtitle,
  kicker,
  parcels = SAMPLE,
  highlight,
}: {
  title: string;
  subtitle: string;
  kicker?: string;
  parcels?: MapParcel[];
  highlight?: string;
}) {
  const map = buildVillageMap(parcels.length ? parcels : SAMPLE, 520, 600);
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", background: COLORS.paper, fontFamily: "Atkinson" }}>
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", width: 680, padding: "56px 48px 52px 64px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <MarkSvg size={56} />
            <span style={{ fontSize: 34, fontWeight: 800, color: COLORS.ink, letterSpacing: -0.5 }}>LandChain</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
            {kicker ? <span style={{ fontSize: 26, color: COLORS.ink2, fontWeight: 700 }}>{kicker}</span> : null}
            <span style={{ fontSize: title.length > 40 ? 56 : 66, fontWeight: 800, color: COLORS.ink, lineHeight: 1.05, letterSpacing: -1.5 }}>
              {title}
            </span>
            <span style={{ fontSize: 26, color: COLORS.ink2, lineHeight: 1.4 }}>{subtitle}</span>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            {[COLORS.reg, COLORS.field, COLORS.violet].map((c, i) => (
              <span key={c} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 20, color: COLORS.ink2, marginRight: 18 }}>
                <span style={{ width: 14, height: 14, borderRadius: 7, background: c }} />
                {["Registration", "Revenue", "Collectorate"][i]}
              </span>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", flex: 1, padding: "15px 15px 15px 0" }}>
          <svg width={map.width} height={map.height} viewBox={`0 0 ${map.width} ${map.height}`} style={{ borderRadius: 18 }}>
            <rect width={map.width} height={map.height} fill="#F6F8F4" />
            {map.villages.flatMap((v) => [
              ...v.cells.map((c, i) => {
                const pending = c.parcel?.status === "TRANSFER_PENDING";
                const hl = highlight && c.parcel?.surveyNo === highlight;
                return (
                  <path
                    key={`${v.name}-${i}`}
                    d={pathOf(c.poly)}
                    fill={hl ? "#D9CFF5" : c.parcel ? (pending ? COLORS.ochreWash : COLORS.fieldWash) : "#F6F8F4"}
                    stroke={hl ? COLORS.violet : pending ? COLORS.ochre : "#9AA7A0"}
                    strokeWidth={hl ? 5 : c.parcel ? 1.6 : 1.1}
                  />
                );
              }),
              <path key={`${v.name}-edge`} d={pathOf(v.poly)} fill="none" stroke="#4B5A52" strokeWidth={3} />,
            ])}
          </svg>
        </div>
      </div>
    ),
    { ...SHARE_SIZE, fonts: await fonts() },
  );
}
