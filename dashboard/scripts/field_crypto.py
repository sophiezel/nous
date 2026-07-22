"""AES-256-GCM field encryption for report content.
Shared key from SQLCIPHER_KEY env var (SOPS-managed).
Used by report_store.py (writer) and db.ts wrapper (reader).
"""
import os
import base64
import hashlib

ALGORITHM = "aes-256-gcm"
SALT = b"hermes-dashboard-v2"  # matches crypto-client.ts KEY_DERIVATION_SALT

def _get_key():
    key_hex = os.environ.get("SQLCIPHER_KEY", "")
    if len(key_hex) != 64:
        raise RuntimeError("SQLCIPHER_KEY not set (need 64 hex chars)")
    return bytes.fromhex(key_hex)

def encrypt_content(plaintext: str) -> str:
    """Encrypt report content → base64 string for SQLite TEXT column."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # Format: base64(nonce + ciphertext)
    combined = nonce + ct
    return base64.b64encode(combined).decode("ascii")

def decrypt_content(encrypted: str) -> str:
    """Decrypt base64-encoded content back to plaintext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    
    key = _get_key()
    aesgcm = AESGCM(key)
    data = base64.b64decode(encrypted)
    nonce, ct = data[:12], data[12:]
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
