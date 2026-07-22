import { getSessionCryptoKey } from "./crypto-client";

export async function signRequest(url: string): Promise<string> {
  const key = await getSessionCryptoKey();
  const ts = Date.now().toString();
  const pathname = new URL(url, window.location.origin).pathname;
  const message = `${ts}:${pathname}`;

  // Export AES key as raw bytes for HMAC
  const rawKey = await crypto.subtle.exportKey("raw", key);

  const hmacKey = await crypto.subtle.importKey(
    "raw",
    rawKey,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );

  const sig = await crypto.subtle.sign(
    "HMAC",
    hmacKey,
    new TextEncoder().encode(message)
  );

  const sigHex = Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  return `${ts}:${sigHex}`;
}
