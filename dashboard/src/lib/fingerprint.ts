import FingerprintJS from "@fingerprintjs/fingerprintjs";

let fpPromise: Promise<string> | null = null;

export async function getDeviceFingerprint(): Promise<string> {
  if (fpPromise) return fpPromise;

  fpPromise = (async () => {
    if (typeof window === "undefined") return "";

    const cached = sessionStorage.getItem("__hfp");
    if (cached) return cached;

    try {
      const fp = await FingerprintJS.load();
      const result = await fp.get();

      const salted = await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(result.visitorId + "__hermes_salt_v2")
      );
      const hash = Array.from(new Uint8Array(salted))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");

      sessionStorage.setItem("__hfp", hash);
      return hash;
    } catch {
      // fallback: use a time-based token if fingerprinting fails
      const fallback = "fp_" + Date.now().toString(36);
      sessionStorage.setItem("__hfp", fallback);
      return fallback;
    }
  })();

  return fpPromise;
}
