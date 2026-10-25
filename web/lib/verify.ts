/**
 * Verify a validator report in the browser, trusting nothing but the ledger:
 *
 *   1  the validator's public key, as attested on the ledger by a Revenue officer
 *   2  the OpenPGP signature over the exact report bytes (OpenPGP.js, here)
 *   3  the signing key is that attested key
 *   4  the report's SHA-256 is anchored on the ledger, by that key
 *
 * The same checks as  gpg --verify report.json.asc report.json  plus the ledger.
 */

import { sha256 } from "@noble/hashes/sha2.js";

import { api } from "./api";
import { toHex } from "./did";
import type { AgentKeyRecord, ValidationAnchor } from "./types";

export type StepState = "pending" | "running" | "ok" | "fail" | "skip";

export interface VerifyStep {
  id: "key" | "signature" | "attested" | "anchor";
  label: string;
  state: StepState;
  detail?: string;
}

export interface ReportSummary {
  reportId?: string;
  applicationId?: string;
  surveyNo?: string;
  verdict?: { status?: string; flagCount?: number };
  generatedAt?: string;
  validator?: { name?: string; pgpFingerprint?: string };
}

export interface VerifyOutcome {
  valid: boolean;
  sha256: string;
  steps: VerifyStep[];
  signerFingerprint?: string;
  key?: AgentKeyRecord | null;
  anchor?: (ValidationAnchor & { anchored: true }) | { anchored: false; sha256: string } | null;
  report?: ReportSummary | null;
  reason?: string;
}

export function sha256Hex(bytes: Uint8Array): string {
  return toHex(sha256(bytes));
}

const LABELS: Record<VerifyStep["id"], string> = {
  key: "Read the validator's attested key from the ledger",
  signature: "Check the OpenPGP signature over these exact bytes",
  attested: "Confirm the signing key is the one Revenue attested",
  anchor: "Find the report's fingerprint anchored on the ledger",
};

export async function verifyReport(
  reportBytes: Uint8Array,
  armoredSignature: string,
  onProgress?: (steps: VerifyStep[]) => void,
): Promise<VerifyOutcome> {
  const steps: VerifyStep[] = (Object.keys(LABELS) as VerifyStep["id"][]).map((id) => ({ id, label: LABELS[id], state: "pending" }));
  const set = (id: VerifyStep["id"], state: StepState, detail?: string) => {
    const s = steps.find((x) => x.id === id)!;
    s.state = state;
    s.detail = detail;
    onProgress?.(steps.map((x) => ({ ...x })));
  };
  const digest = sha256Hex(reportBytes);
  const out: VerifyOutcome = { valid: false, sha256: digest, steps };

  let report: ReportSummary | null = null;
  try {
    report = JSON.parse(new TextDecoder().decode(reportBytes)) as ReportSummary;
  } catch {
    report = null;
  }
  out.report = report;

  // 1. the attested key
  set("key", "running");
  let record: AgentKeyRecord | null = null;
  try {
    const res = await api<{ publicKey: string | null; onChain: AgentKeyRecord | null }>("/api/public/agent-key");
    record = res.onChain;
    if (!record?.publicKey) throw new Error("No validator key is attested on the ledger yet.");
    out.key = record;
    set("key", "ok", `${record.agentName}: key ${record.pgpFingerprint}, attested by ${record.attestedBy} (${record.status})`);
  } catch (e) {
    set("key", "fail", (e as Error).message);
    out.reason = "The ledger could not be read, so there is no trusted key to check against.";
    return finish(out, steps, onProgress);
  }

  // 2. the signature
  set("signature", "running");
  const openpgp = await import("openpgp");
  let signerKeyId = "";
  try {
    const signature = await openpgp.readSignature({ armoredSignature: armoredSignature.trim() });
    signerKeyId = signature.getSigningKeyIDs()[0]?.toHex().toUpperCase() ?? "";
    const candidates = [record.publicKey, ...((record.previousKeys ?? []) as { publicKey?: string }[]).map((k) => k.publicKey)].filter(
      (k): k is string => Boolean(k),
    );
    let verified = false;
    let lastError = "the signature was made by a key the ledger does not list";
    for (const armoredKey of candidates) {
      const key = await openpgp.readKey({ armoredKey });
      const fpr = key.getFingerprint().toUpperCase();
      if (!fpr.endsWith(signerKeyId)) continue;
      const message = await openpgp.createMessage({ binary: reportBytes });
      const result = await openpgp.verify({ message, signature, verificationKeys: key, format: "binary" });
      try {
        await result.signatures[0].verified;
        verified = true;
        out.signerFingerprint = fpr;
      } catch (e) {
        lastError = (e as Error).message;
      }
      break;
    }
    if (!verified) throw new Error(lastError);
    set("signature", "ok", `Good signature from key ${out.signerFingerprint} over ${reportBytes.length} bytes`);
  } catch (e) {
    const msg = (e as Error).message || "invalid signature";
    set("signature", "fail", /signed digest did not match|invalid signature/i.test(msg)
      ? "BAD signature: these bytes are not the bytes the validator signed (the report was changed, or the wrong file was chosen)."
      : msg);
    out.reason = "The signature does not match this report.";
    set("attested", "skip");
    await anchorStep(out, set, digest);
    return finish(out, steps, onProgress);
  }

  // 3. the key is the attested one
  const current = record.pgpFingerprint.toUpperCase();
  const previous = (record.previousKeys ?? []).find((k) => k.pgpFingerprint.toUpperCase() === out.signerFingerprint);
  if (out.signerFingerprint === current && record.status === "ACTIVE") {
    set("attested", "ok", `Attested on the ledger by ${record.attestedBy} on ${record.attestedAt.slice(0, 10)}`);
  } else if (previous) {
    set("attested", "ok", `An earlier key of ${record.agentName}, attested by ${previous.attestedBy}, rotated ${previous.rotatedAt.slice(0, 10)}`);
  } else {
    set("attested", "fail", `Signed by ${out.signerFingerprint}, which is not the validator's attested key ${current}`);
    out.reason = "The signer is not the attested validator.";
  }

  // 4. anchored on the ledger
  await anchorStep(out, set, digest);
  out.valid = steps.every((s) => s.state === "ok");
  if (out.valid) out.reason = undefined;
  return finish(out, steps, onProgress);
}

async function anchorStep(out: VerifyOutcome, set: (id: VerifyStep["id"], s: StepState, d?: string) => void, digest: string) {
  set("anchor", "running");
  try {
    const anchor = await api<VerifyOutcome["anchor"]>(`/api/public/verify/report/${digest}`);
    out.anchor = anchor;
    if (anchor && anchor.anchored) {
      const sameKey = !out.signerFingerprint || anchor.pgpFingerprint.toUpperCase() === out.signerFingerprint;
      set(
        "anchor",
        sameKey ? "ok" : "fail",
        sameKey
          ? `SHA-256 ${digest.slice(0, 16)}… anchored for ${anchor.appId} at ${anchor.anchoredAt} (tx ${anchor.txId.slice(0, 12)}…)`
          : `Anchored, but under key ${anchor.pgpFingerprint}`,
      );
      if (!sameKey) out.reason = out.reason ?? "The anchor names a different key.";
    } else {
      set("anchor", "fail", `SHA-256 ${digest.slice(0, 16)}… is not on the ledger.`);
      out.reason = out.reason ?? "This exact report was never anchored on the ledger.";
    }
  } catch (e) {
    set("anchor", "fail", (e as Error).message);
    out.reason = out.reason ?? "The ledger could not be read.";
  }
}

function finish(out: VerifyOutcome, steps: VerifyStep[], onProgress?: (s: VerifyStep[]) => void): VerifyOutcome {
  out.steps = steps.map((s) => ({ ...s, state: s.state === "pending" ? "skip" : s.state }));
  onProgress?.(out.steps);
  return out;
}

/** A document file's fingerprint, checked against the ledger's document anchors. */
export async function verifyDocument(bytes: Uint8Array) {
  const digest = sha256Hex(bytes);
  const res = await api<
    | { anchored: true; sha256: string; appId: string; docId: string; documentType: string; issuer: string; anchoredBy: string; anchoredAt?: string; txId?: string }
    | { anchored: false; sha256: string }
  >(`/api/public/verify/document/${digest}`);
  return { digest, ...res };
}
