/** Formatting shared by every page: money, dates, identifiers, status words. */

import type { AppStatus, MspId, ParcelStatus, Verdict } from "./types";

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const num = new Intl.NumberFormat("en-IN");

/** 3850000 -> "₹38,50,000" (Indian digit grouping). */
export function rupees(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? "—" : inr.format(value);
}

export function number(value: number | null | undefined): string {
  return value == null ? "—" : num.format(value);
}

export function area(sqm: number | null | undefined): string {
  return sqm == null ? "—" : `${num.format(Math.round(sqm))} sq.m`;
}

const dateTime = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Asia/Kolkata",
});
const dateOnly = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });

/** "5 Oct 2026, 12:46 IST" -- the offices are in India, so times are shown in IST. */
export function when(iso: string | number | null | undefined): string {
  const d = toDate(iso);
  return d ? `${dateTime.format(d)} IST` : "—";
}

export function day(iso: string | number | null | undefined): string {
  const d = toDate(iso);
  return d ? dateOnly.format(d) : "—";
}

export function toDate(iso: string | number | null | undefined): Date | null {
  if (iso == null || iso === "") return null;
  const d = typeof iso === "number" ? new Date(iso < 1e12 ? iso * 1000 : iso) : new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "3 min ago", "2 h ago", "yesterday". */
export function ago(iso: string | number | null | undefined, now = Date.now()): string {
  const d = toDate(iso);
  if (!d) return "—";
  const s = Math.round((now - d.getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const days = Math.round(h / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

/** Groups of four, so two fingerprints can be compared by eye. */
export function chunk(value: string, size = 4): string {
  return value.replace(new RegExp(`(.{${size}})(?=.)`, "g"), "$1 ");
}

export function shortHash(value: string | null | undefined, head = 8, tail = 6): string {
  if (!value) return "—";
  return value.length <= head + tail + 1 ? value : `${value.slice(0, head)}…${value.slice(-tail)}`;
}

// ---- words ---------------------------------------------------------------------

export const ORG_NAME: Record<MspId, string> = {
  Org1MSP: "Registration",
  Org2MSP: "Revenue",
  Org3MSP: "Collectorate",
};

export const ROLE_TITLE: Record<string, string> = {
  registrar: "Sub-Registrar",
  clerk: "Registration Clerk",
  verifier: "Revenue Verifier",
  surveyor: "Mandal Surveyor",
  collector: "District Collector",
  agent: "AI validator",
  portal: "Citizen portal",
};

export type Tone = "reg" | "rev" | "col" | "ok" | "warn" | "bad" | "muted";

export const STATUS_INFO: Record<AppStatus, { label: string; tone: Tone; where: string }> = {
  SUBMITTED: { label: "Filed at Registration", tone: "reg", where: "Registration" },
  WITH_REVENUE: { label: "With Revenue", tone: "rev", where: "Revenue" },
  DOCS_VERIFIED: { label: "Documents verified", tone: "rev", where: "Revenue" },
  SURVEYED: { label: "Surveyed", tone: "rev", where: "Revenue" },
  WITH_COLLECTORATE: { label: "With the Collector", tone: "col", where: "Collectorate" },
  FLAGGED: { label: "Flagged", tone: "bad", where: "Collectorate" },
  APPROVED: { label: "Approved", tone: "ok", where: "Done" },
  REJECTED: { label: "Rejected", tone: "bad", where: "Done" },
};

/** The order a file moves in (FLAGGED is a side exit after verification). */
export const LIFECYCLE: AppStatus[] = [
  "SUBMITTED",
  "WITH_REVENUE",
  "DOCS_VERIFIED",
  "SURVEYED",
  "WITH_COLLECTORATE",
  "APPROVED",
];

export const PARCEL_STATUS: Record<ParcelStatus, { label: string; tone: Tone }> = {
  ACTIVE: { label: "Registered", tone: "ok" },
  TRANSFER_PENDING: { label: "Transfer under way", tone: "warn" },
};

export const VERDICT_INFO: Record<Verdict, { label: string; tone: Tone; plain: string }> = {
  clear: { label: "Clear", tone: "ok", plain: "Every check the validator could run passed." },
  attention: { label: "Needs attention", tone: "warn", plain: "Some checks need an officer to look closer." },
  flagged: { label: "Flagged", tone: "bad", plain: "At least one check failed." },
};

export const DOC_TYPE: Record<string, string> = {
  SALEDEED: "Sale deed",
  ENCUMBRANCE: "Encumbrance certificate",
  PANCR: "PAN card",
};

export function docType(t: string): string {
  return DOC_TYPE[t] ?? t;
}

export const TX_CODE_LABEL: Record<string, string> = {
  VALID: "Valid",
  MVCC_READ_CONFLICT: "Read conflict",
  PHANTOM_READ_CONFLICT: "Phantom read",
  ENDORSEMENT_POLICY_FAILURE: "Endorsement policy failed",
  DUPLICATE_TXID: "Duplicate transaction",
  BAD_CREATOR_SIGNATURE: "Bad signature",
};

/** "SY-5D0E-12/4A" -> "/register/SY-5D0E-12/4A" (survey numbers keep their slash). */
export function parcelPath(surveyNo: string, base = "/register"): string {
  return `${base}/${surveyNo.split("/").map(encodeURIComponent).join("/")}`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${number(n)} ${n === 1 ? one : many}`;
}
