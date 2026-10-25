/**
 * Canonical JSON -- byte-for-byte the same as Python's
 *   json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 * which the gateway and the chaincode use (chaincode/landrec/landrec.py: canonical).
 *
 * Signatures are over bytes, so the browser and the chaincode must agree on
 * every byte: keys sorted, no spaces, UTF-8, integers without ".0".
 */

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export function canonicalString(value: unknown): string {
  return write(value as Json);
}

export function canonicalBytes(value: unknown): Uint8Array {
  return new TextEncoder().encode(canonicalString(value));
}

function write(value: Json): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return `[${value.map(write).join(",")}]`;
  switch (typeof value) {
    case "number":
      if (!Number.isFinite(value)) throw new Error("canonical JSON cannot hold NaN or Infinity");
      return JSON.stringify(value);
    case "boolean":
    case "string":
      return JSON.stringify(value);
    case "object": {
      const keys = Object.keys(value)
        .filter((k) => value[k] !== undefined)
        .sort(byCodePoint);
      return `{${keys.map((k) => `${JSON.stringify(k)}:${write(value[k])}`).join(",")}}`;
    }
    default:
      throw new Error(`cannot serialise ${typeof value}`);
  }
}

/** Python sorts str keys by code point; JS sorts by UTF-16 unit. Same for every key we use, but be exact. */
function byCodePoint(a: string, b: string): number {
  const x = Array.from(a);
  const y = Array.from(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0)! - y[i].codePointAt(0)!;
    if (d) return d;
  }
  return x.length - y.length;
}
