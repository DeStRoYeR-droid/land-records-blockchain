/**
 * Transfer records in the browser: download them, and verify one without trusting this website.
 *
 *   1  the Records Office key, and the fingerprint the Collector attested on the ledger
 *   2  the OpenPGP cleartext signature over the record's text (OpenPGP.js, here)
 *   3  the signing key is the attested key
 *   4  the record names a transfer file that exists on the ledger
 *
 * The same as  gpg --verify landchain-APP-...-public.asc  plus the ledger.
 */

import { api, apiBytes, download, type Token } from "./api";

export interface RecordsKeyInfo {
  publicKey: string;
  pgpFingerprint: string;
  userId: string;
  onLedger: { pgpFingerprint: string; status: string; attestedBy: string; attestedAt: string } | null;
}

export interface RecordCheck {
  valid: boolean;
  steps: { label: string; ok: boolean | null; detail?: string }[];
  recordId?: string;
  copy?: string;
  applicationId?: string;
  issuedAt?: string;
  issuedTo?: string;
  signer?: string;
}

export function publicRecordPath(appId: string): string {
  return `/api/public/records/${encodeURIComponent(appId)}`;
}

export async function downloadRecord(path: string, fallbackName: string, token?: Token): Promise<string> {
  const bytes = await apiBytes(path, token);
  const text = new TextDecoder().decode(bytes);
  download(fallbackName, text, "text/plain");
  return text;
}

/** The canonical JSON at the end of a record (its fixed-width lines joined again). */
export function recordData(text: string): Record<string, unknown> | null {
  const marker = "MACHINE-READABLE COPY (CANONICAL JSON)";
  const at = text.indexOf(marker);
  if (at < 0) return null;
  const lines = text.slice(at).split("\n").slice(2);
  const body: string[] = [];
  for (const l of lines) {
    if (l.startsWith("-- end of record --")) break;   // getText() has already undone the dash-escaping
    body.push(l);
  }
  try {
    return JSON.parse(body.join(""));
  } catch {
    return null;
  }
}

export async function verifyRecord(message: string): Promise<RecordCheck> {
  const steps: RecordCheck["steps"] = [];
  const key = await api<RecordsKeyInfo>("/api/public/records-key");
  steps.push({
    label: "Read the Records Office key and its attestation on the ledger",
    ok: Boolean(key.onLedger),
    detail: key.onLedger ? `attested by ${key.onLedger.attestedBy}` : "the key is not attested on the ledger",
  });
  const openpgp = await import("openpgp");
  let signedText = "";
  let signer = "";
  try {
    const msg = await openpgp.readCleartextMessage({ cleartextMessage: message.trim() });
    const verificationKeys = await openpgp.readKey({ armoredKey: key.publicKey });
    const res = await openpgp.verify({ message: msg, verificationKeys });
    const sig = res.signatures[0];
    await sig.verified;
    signer = verificationKeys.getFingerprint().toUpperCase();
    signedText = msg.getText();
    steps.push({ label: "Check the OpenPGP signature over the record's text", ok: true, detail: "Good signature" });
  } catch (e) {
    steps.push({ label: "Check the OpenPGP signature over the record's text", ok: false, detail: (e as Error).message.slice(0, 160) });
    return { valid: false, steps };
  }
  const attested = Boolean(key.onLedger && key.onLedger.pgpFingerprint === signer);
  steps.push({ label: "Confirm the signing key is the one the Collector attested", ok: attested, detail: signer });
  const data = recordData(signedText) as {
    recordId?: string; copy?: string; issuedAt?: string; transfer?: { applicationId?: string }; issuedTo?: { name?: string; role?: string };
  } | null;
  const appId = data?.transfer?.applicationId;
  let found: boolean | null = null;
  if (appId) {
    try {
      const v = await api<{ applicationId: string | null; currentStatus?: string | null }>("/api/public/records/verify", { method: "POST", body: { record: message } });
      found = Boolean(v.currentStatus);
      steps.push({ label: "Find the transfer file on the ledger", ok: found, detail: v.currentStatus ? `${appId} is now ${v.currentStatus}` : `${appId} not found` });
    } catch {
      steps.push({ label: "Find the transfer file on the ledger", ok: null, detail: "the ledger could not be asked just now" });
    }
  }
  return {
    valid: steps.every((s) => s.ok !== false), steps, recordId: data?.recordId, copy: data?.copy, applicationId: appId,
    issuedAt: data?.issuedAt, issuedTo: data?.issuedTo ? `${data.issuedTo.name} (${data.issuedTo.role})` : undefined, signer,
  };
}
