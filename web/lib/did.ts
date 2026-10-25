/**
 * The citizen's signing key, made and kept in this browser.
 *
 *   did:key:z6Mk...  =  "did:key:" + "z" + base58btc( 0xed 0x01 || 32-byte Ed25519 public key )
 *
 * The same encoding as chaincode/landrec/didkey.py, so the chaincode can turn
 * the DID back into the public key and check the signatures itself.
 *
 * Uses @noble/ed25519 with @noble/hashes (pure JavaScript) rather than
 * WebCrypto, because WebCrypto is switched off on plain-http origins such as
 * http://192.168.1.20:3000 -- and teammates will open the dev server that way.
 */

import * as ed from "@noble/ed25519";
import { sha512 } from "@noble/hashes/sha2.js";

import { canonicalBytes } from "./canonical";

ed.hashes.sha512 = sha512;

const ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const ED25519_PREFIX = [0xed, 0x01];

export function base58encode(bytes: Uint8Array): string {
  let n = 0n;
  for (const b of bytes) n = (n << 8n) | BigInt(b);
  let out = "";
  while (n > 0n) {
    out = ALPHABET[Number(n % 58n)] + out;
    n /= 58n;
  }
  for (const b of bytes) {
    if (b !== 0) break;
    out = "1" + out;
  }
  return out;
}

export function base58decode(text: string): Uint8Array {
  let n = 0n;
  for (const ch of text) {
    const i = ALPHABET.indexOf(ch);
    if (i < 0) throw new Error(`invalid base58 character ${ch}`);
    n = n * 58n + BigInt(i);
  }
  const bytes: number[] = [];
  while (n > 0n) {
    bytes.unshift(Number(n & 0xffn));
    n >>= 8n;
  }
  for (const ch of text) {
    if (ch !== "1") break;
    bytes.unshift(0);
  }
  return new Uint8Array(bytes);
}

export function didFromPublicKey(pub: Uint8Array): string {
  return `did:key:z${base58encode(new Uint8Array([...ED25519_PREFIX, ...pub]))}`;
}

export function publicKeyFromDid(did: string): Uint8Array {
  if (!did.startsWith("did:key:z")) throw new Error("not a did:key");
  const raw = base58decode(did.slice("did:key:z".length));
  if (raw.length !== 34 || raw[0] !== 0xed || raw[1] !== 0x01) throw new Error("not an Ed25519 did:key");
  return raw.slice(2);
}

export function toHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function fromHex(hex: string): Uint8Array {
  const clean = hex.trim().toLowerCase();
  if (!/^([0-9a-f]{2})*$/.test(clean)) throw new Error("not hex");
  return new Uint8Array(clean.match(/../g)?.map((h) => parseInt(h, 16)) ?? []);
}

export function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

export interface CitizenKey {
  did: string;
  secretKeyHex: string;
  createdAt: string;
}

export function generateKey(): CitizenKey {
  const secretKey = ed.utils.randomSecretKey();
  const publicKey = ed.getPublicKey(secretKey);
  return { did: didFromPublicKey(publicKey), secretKeyHex: toHex(secretKey), createdAt: new Date().toISOString() };
}

/** Sign the canonical JSON of `payload`; returns standard base64 (what the gateway expects). */
export function signCanonical(key: CitizenKey, payload: unknown): string {
  const sig = ed.sign(canonicalBytes(payload), fromHex(key.secretKeyHex));
  return toBase64(sig);
}

/** True if the key file really holds the private key behind its DID. */
export function keyMatchesDid(key: CitizenKey): boolean {
  try {
    return didFromPublicKey(ed.getPublicKey(fromHex(key.secretKeyHex))) === key.did;
  } catch {
    return false;
  }
}

// ---- storage in this browser -------------------------------------------------

const KEY_PREFIX = "landchain.key.";
const ACCOUNTS = "landchain.accounts";

export interface KnownAccount {
  digilockerId: string;
  did: string;
  name: string;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function saveKey(key: CitizenKey): void {
  storage()?.setItem(KEY_PREFIX + key.did, JSON.stringify(key));
}

export function loadKey(did: string): CitizenKey | null {
  try {
    const raw = storage()?.getItem(KEY_PREFIX + did);
    const key = raw ? (JSON.parse(raw) as CitizenKey) : null;
    return key && keyMatchesDid(key) ? key : null;
  } catch {
    return null;
  }
}

export function forgetKey(did: string): void {
  storage()?.removeItem(KEY_PREFIX + did);
  saveAccounts(knownAccounts().filter((a) => a.did !== did));
}

export function knownAccounts(): KnownAccount[] {
  try {
    return JSON.parse(storage()?.getItem(ACCOUNTS) || "[]") as KnownAccount[];
  } catch {
    return [];
  }
}

function saveAccounts(list: KnownAccount[]): void {
  storage()?.setItem(ACCOUNTS, JSON.stringify(list));
}

export function rememberAccount(acc: KnownAccount): void {
  saveAccounts([acc, ...knownAccounts().filter((a) => a.digilockerId !== acc.digilockerId)].slice(0, 12));
}

/** The JSON file a citizen downloads to keep (or move) their key. */
export function keyFile(key: CitizenKey, account: Partial<KnownAccount>): string {
  return JSON.stringify(
    {
      type: "landchain.citizen-key/v1",
      warning: "This file is your private signing key. Anyone who has it can sign transfers as you. Keep it offline.",
      did: key.did,
      secretKeyHex: key.secretKeyHex,
      createdAt: key.createdAt,
      digilockerId: account.digilockerId ?? null,
      name: account.name ?? null,
    },
    null,
    2,
  );
}

export function parseKeyFile(text: string): { key: CitizenKey; account: Partial<KnownAccount> } {
  const data = JSON.parse(text) as Record<string, string>;
  if (data.type !== "landchain.citizen-key/v1" || !data.did || !data.secretKeyHex) {
    throw new Error("This is not a LandChain key file.");
  }
  const key: CitizenKey = { did: data.did, secretKeyHex: data.secretKeyHex, createdAt: data.createdAt };
  if (!keyMatchesDid(key)) throw new Error("The key in this file does not match its DID; the file is damaged.");
  return { key, account: { digilockerId: data.digilockerId || undefined, name: data.name || undefined, did: data.did } };
}
