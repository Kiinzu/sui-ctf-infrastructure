#!/usr/bin/env bash
#
# End-to-end validation of the challenge pipeline WITHOUT Docker, using a local
# `sui` CLI. Boots an isolated localnet, publishes + seeds the placeholder
# challenge, solves it, and runs the orchestrator's own solve-check code.
#
# Everything runs under ./tmp and is cleaned up on exit. Requires: sui, jq, curl,
# python3.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RPC_PORT="${RPC_PORT:-9010}"
FAUCET_PORT="${FAUCET_PORT:-9133}"
PKG="$REPO/challenges/placeholder-flashpool/package"
WORK="$REPO/tmp/localtest"
SUIHOME="$WORK/suihome"

command -v sui  >/dev/null || { echo "sui CLI not found"; exit 1; }
command -v jq   >/dev/null || { echo "jq not found"; exit 1; }
command -v curl >/dev/null || { echo "curl not found"; exit 1; }

rm -rf "$WORK"; mkdir -p "$SUIHOME"
export HOME="$SUIHOME"
cd "$WORK"   # keep ephemeral artifacts (Pub.*.toml) out of the repo root

echo "[*] booting localnet (rpc :$RPC_PORT, faucet :$FAUCET_PORT)"
sui start --with-faucet="0.0.0.0:$FAUCET_PORT" --force-regenesis \
  --fullnode-rpc-port "$RPC_PORT" > "$WORK/sui.log" 2>&1 &
SUI_PID=$!
cleanup() { kill "$SUI_PID" 2>/dev/null || true; }
trap cleanup EXIT

rpc() { curl -s -X POST "http://127.0.0.1:$RPC_PORT" -H 'content-type: application/json' -d "$1"; }
balance_of() { rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"sui_getObject\",\"params\":[\"$1\",{\"showContent\":true}]}" | jq -r '.result.data.content.fields.balance'; }
faucet() { curl -s -X POST "http://127.0.0.1:$FAUCET_PORT/gas" -H 'content-type: application/json' -d "{\"FixedAmountRequest\":{\"recipient\":\"$1\"}}" >/dev/null || true; }

echo "[*] waiting for RPC..."
tries=0
until rpc '{"jsonrpc":"2.0","id":1,"method":"sui_getChainIdentifier","params":[]}' | grep -q '"result"'; do
  tries=$((tries + 1)); [ "$tries" -gt 120 ] && { echo "timeout"; cat "$WORK/sui.log"; exit 1; }
  kill -0 "$SUI_PID" 2>/dev/null || { echo "sui died"; cat "$WORK/sui.log"; exit 1; }
  sleep 1
done

CFG="$SUIHOME/.sui/sui_config"; mkdir -p "$CFG"; echo '[]' > "$CFG/sui.keystore"
cat > "$CFG/client.yaml" <<EOF
keystore:
  File: $CFG/sui.keystore
envs:
  - alias: ctf
    rpc: "http://127.0.0.1:$RPC_PORT"
    ws: ~
    basic_auth: ~
active_env: ctf
active_address: ~
EOF

# keep requesting the faucet until gas appears (the faucet can lag RPC readiness)
fund() { local a="$1" t=0; until [ "$(sui client gas "$a" --json 2>/dev/null | jq 'length' 2>/dev/null || echo 0)" -gt 0 ]; do faucet "$a"; t=$((t+1)); [ "$t" -gt 60 ] && return 1; sleep 1; done; }

sui client new-address ed25519 deployer --json >/dev/null
sui client new-address ed25519 player --json >/dev/null
DEPLOYER="$(sui client addresses --json | jq -r '.addresses[]|select(.[0]=="deployer")|.[1]')"
PLAYER="$(sui client addresses --json | jq -r '.addresses[]|select(.[0]=="player")|.[1]')"
echo "[*] deployer=$DEPLOYER"
echo "[*] player=$PLAYER"

fund "$DEPLOYER" || { echo "no deployer gas"; exit 1; }
sui client switch --address deployer >/dev/null

echo "[*] publishing challenge package"
sui client test-publish "$PKG" --build-env ctf --json --skip-dependency-verification > "$WORK/pub.json" 2>"$WORK/pub.err" || { cat "$WORK/pub.err"; exit 1; }
PACKAGE_ID="$(jq -r '.objectChanges[]?|select(.type=="published")|.packageId' "$WORK/pub.json")"
echo "[*] package_id=$PACKAGE_ID"

echo "[*] seeding pool (init_pool 1000000)"
sui client call --package "$PACKAGE_ID" --module pool --function init_pool --args 1000000 --json --gas-budget 500000000 > "$WORK/seed.json" 2>/dev/null
POOL_ID="$(jq -r '.objectChanges[]?|select(.type=="created" and (.objectType|test("::pool::Pool$")))|.objectId' "$WORK/seed.json")"
POOL_TYPE="$(jq -r '.objectChanges[]?|select(.type=="created" and (.objectType|test("::pool::Pool$")))|.objectType' "$WORK/seed.json")"
BAL_BEFORE="$(balance_of "$POOL_ID")"
echo "[*] pool=$POOL_ID balance_before=$BAL_BEFORE (expect 1000000)"

echo "[*] seeding a second, UNSOLVED pool (negative case)"
sui client call --package "$PACKAGE_ID" --module pool --function init_pool --args 1000000 --json --gas-budget 500000000 > "$WORK/seed2.json" 2>/dev/null
FRESH_ID="$(jq -r '.objectChanges[]?|select(.type=="created" and (.objectType|test("::pool::Pool$")))|.objectId' "$WORK/seed2.json")"
FRESH_TYPE="$POOL_TYPE"

echo "[*] SOLVE as player: drain the first pool"
sui client switch --address player >/dev/null
fund "$PLAYER" || { echo "no player gas"; exit 1; }
sui client call --package "$PACKAGE_ID" --module pool --function drain --args "$POOL_ID" --json --gas-budget 500000000 >/dev/null 2>&1
BAL_AFTER="$(balance_of "$POOL_ID")"
echo "[*] balance_after=$BAL_AFTER (expect 0)"

echo "[*] running orchestrator solve-check code against the live node"
VENV="$REPO/tmp/venv"
if [ ! -x "$VENV/bin/python" ]; then python3 -m venv "$VENV"; "$VENV/bin/pip" -q install httpx pyyaml; fi
RC=0
RPC_URL="http://127.0.0.1:$RPC_PORT/" PACKAGE_ID="$PACKAGE_ID" \
  DRAINED_POOL_ID="$POOL_ID" DRAINED_POOL_TYPE="$POOL_TYPE" \
  FRESH_POOL_ID="$FRESH_ID" FRESH_POOL_TYPE="$FRESH_TYPE" \
  "$VENV/bin/python" "$REPO/scripts/solvecheck_probe.py" || RC=$?

echo "------------------------------------------------------------"
if [ "$BAL_BEFORE" = "1000000" ] && [ "$BAL_AFTER" = "0" ] && [ "$RC" -eq 0 ]; then
  echo "LOCAL PIPELINE: PASS ✅"
else
  echo "LOCAL PIPELINE: FAIL ❌ (before=$BAL_BEFORE after=$BAL_AFTER probe_rc=$RC)"
  exit 1
fi
