#!/usr/bin/env python3
"""Decrypt SQLCIPHER_KEY and print export statement for shell eval."""
import subprocess, os

home = os.path.expanduser("~")
result = subprocess.run(
    ["sops", "--decrypt", f"{home}/.hermes/secrets/sqlcipher.enc.env"],
    capture_output=True, text=True,
    env={**os.environ, "SOPS_AGE_KEY_FILE": f"{home}/.age/hermes-key.txt"}
)
if result.returncode != 0:
    print(f"echo 'SOPS decrypt failed: {result.stderr[:100]}' >&2", flush=True)
    exit(1)
print(result.stdout.strip(), flush=True)
