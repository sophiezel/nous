const SALT = new TextEncoder().encode("hermes-dashboard-v2");

let cachedKey: CryptoKey | null = null;

function getSessionToken(): string {
  const match = document.cookie.match(
    /(?:^|;\s*)__hermes_session=([^;]*)/
  );
  return match?.[1] || "";
}

export async function getSessionCryptoKey(): Promise<CryptoKey> {
  if (cachedKey) return cachedKey;

  // Try sessionStorage cache
  const jwkStr = sessionStorage.getItem("__hck");
  if (jwkStr) {
    try {
      cachedKey = await crypto.subtle.importKey(
        "jwk",
        JSON.parse(jwkStr),
        { name: "AES-GCM", length: 256 },
        false,
        ["decrypt"]
      );
      return cachedKey;
    } catch {
      sessionStorage.removeItem("__hck");
    }
  }

  const sessionToken = getSessionToken();
  if (!sessionToken) throw new Error("No session token");

  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(sessionToken),
    "PBKDF2",
    false,
    ["deriveKey"]
  );

  cachedKey = await crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt: SALT,
      iterations: 100000,
      hash: "SHA-256",
    },
    keyMaterial,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"]
  );

  // Cache JWK for reuse
  const jwk = await crypto.subtle.exportKey("jwk", cachedKey);
  sessionStorage.setItem("__hck", JSON.stringify(jwk));

  return cachedKey;
}

function base64urlToBytes(s: string): Uint8Array<ArrayBuffer> {
  const base64 = s.replace(/-/g, "+").replace(/_/g, "/");
  const padLen = (4 - (base64.length % 4)) % 4;
  const padded = base64 + "=".repeat(padLen);
  const binStr = atob(padded);
  const buf = new ArrayBuffer(binStr.length);
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < binStr.length; i++) {
    bytes[i] = binStr.charCodeAt(i);
  }
  return bytes;
}

export async function decryptPayload(enc: {
  ct: string;
  iv: string;
  tag: string;
}): Promise<unknown> {
  const key = await getSessionCryptoKey();
  const ciphertext = base64urlToBytes(enc.ct);
  const ivBytes = base64urlToBytes(enc.iv);
  const tagBytes = base64urlToBytes(enc.tag);

  const combined = new Uint8Array(ciphertext.length + tagBytes.length);
  combined.set(ciphertext, 0);
  combined.set(tagBytes, ciphertext.length);

  const decrypted = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: ivBytes },
    key,
    combined
  );

  return JSON.parse(new TextDecoder().decode(decrypted));
}
