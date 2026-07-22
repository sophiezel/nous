import { decryptPayload } from "./crypto-client";
import { signRequest } from "./sign-request";
import { getDeviceFingerprint } from "./fingerprint";

let fpCache: string | null = null;

async function getFp(): Promise<string> {
  if (!fpCache) fpCache = await getDeviceFingerprint();
  return fpCache;
}

export async function fetchEncrypted<T = unknown>(
  url: string,
  options?: RequestInit
): Promise<T> {
  const headers: Record<string, string> = {
    ...((options?.headers as Record<string, string>) || {}),
    "X-Device-Fingerprint": await getFp(),
  };

  // Add HMAC signature for API routes
  if (url.startsWith("/api/")) {
    try {
      headers["X-Request-Signature"] = await signRequest(url);
    } catch {
      // No session yet — will get 401 from server
    }
  }

  const res = await fetch(url, { ...options, headers });

  if (!res.ok) {
    if (res.status === 401) {
      // Redirect to login
      window.location.href = "/login";
      throw new Error("Unauthorized");
    }
    throw new Error(`API error: ${res.status}`);
  }

  const body = await res.json();

  // Decrypt if encrypted response
  if (body.enc) {
    return (await decryptPayload(body.enc)) as T;
  }

  return body as T;
}
