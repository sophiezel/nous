import crypto from "crypto";

const ALGORITHM = "aes-256-gcm";
const SALT = "hermes-dashboard-v2";

export function deriveServerKey(sessionToken: string): Buffer {
  return crypto.pbkdf2Sync(sessionToken, SALT, 100000, 32, "sha256");
}

export interface EncryptedPayload {
  ct: string;
  iv: string;
  tag: string;
}

export function encryptPayload(
  data: unknown,
  sessionToken: string
): EncryptedPayload {
  const key = deriveServerKey(sessionToken);
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv);

  const json = JSON.stringify(data);
  const encrypted = Buffer.concat([
    cipher.update(json, "utf8"),
    cipher.final(),
  ]);
  const authTag = cipher.getAuthTag();

  return {
    ct: encrypted.toString("base64url"),
    iv: iv.toString("base64url"),
    tag: authTag.toString("base64url"),
  };
}
