#!/bin/bash
# Decrypt dashboard secrets via age and export as env vars
AGE_KEY_FILE="${AGE_KEY_FILE:-$HOME/.age/hermes-key.txt}"
ENCRYPTED_ENV="${ENCRYPTED_ENV:-$HOME/.hermes/secrets/dashboard.enc.env}"

[ -f "$ENCRYPTED_ENV" ] || { echo "ERROR: $ENCRYPTED_ENV not found" >&2; exit 1; }
[ -f "$AGE_KEY_FILE" ] || { echo "ERROR: $AGE_KEY_FILE not found" >&2; exit 1; }

# Decrypt and eval as export statements
eval "$(age --decrypt -i "$AGE_KEY_FILE" "$ENCRYPTED_ENV" 2>/dev/null | while IFS='=' read -r key value; do
  [ -n "$key" ] && [ -n "$value" ] && echo "export $key='$value'"
done)"
