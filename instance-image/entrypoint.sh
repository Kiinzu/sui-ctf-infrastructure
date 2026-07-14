#!/usr/bin/env bash
#
# Instance entrypoint: boots an isolated Sui network, publishes the challenge
# package, runs the deploy/seed steps, funds a fresh player keypair, and writes
# /instance/ready.json for the orchestrator to read. Then blocks on the node.
#
# Inputs (env, set by the orchestrator):
#   RPC_PORT             fullnode RPC port (default 9000; binds 0.0.0.0)
#   PACKAGE_DIR          absolute path to the Move package to publish
#   DEPLOY_SPEC          JSON array of {module, function, type_args[], args[]}
#   PLAYER_FAUCET_COINS  how many faucet grants the player key receives
#   CHALLENGE            challenge name (for logs)
#
# NOTE: the flag is intentionally NEVER passed to this container. On Sui all
# on-chain state (object fields AND tx inputs) is publicly RPC-readable, so a
# flag placed on-chain leaks pre-solve. on_chain_claim challenges prove the win
# via an emitted event; the backend returns the server-side flag.
set -euo pipefail

export HOME=/root
RPC_PORT="${RPC_PORT:-9000}"
FAUCET_PORT="${FAUCET_PORT:-9123}"     # internal only, never exposed
GAS_BUDGET="${GAS_BUDGET:-5000000000}" # 5 SUI; faucet grants 200 SUI/coin
PLAYER_FAUCET_COINS="${PLAYER_FAUCET_COINS:-2}"
COMMITTEE_SIZE="${COMMITTEE_SIZE:-1}"  # 1 validator: far lighter, fine for a CTF net
DEPLOY_SPEC="${DEPLOY_SPEC:-[]}"
CFG="$HOME/.sui/sui_config"
OUT=/instance
mkdir -p "$OUT" "$CFG"

log() { echo "[entrypoint] $*"; }
fail() { echo "[entrypoint][FATAL] $*" >&2; exit 1; }

# ---------------------------------------------------------------- boot node
log "starting sui network (rpc :$RPC_PORT, faucet :$FAUCET_PORT), challenge=$CHALLENGE"
sui start \
  --with-faucet="127.0.0.1:${FAUCET_PORT}" \
  --force-regenesis \
  --committee-size "${COMMITTEE_SIZE}" \
  --fullnode-rpc-port "${RPC_PORT}" \
  > "$OUT/sui.log" 2>&1 &
SUI_PID=$!
# faucet binds 127.0.0.1 (not 0.0.0.0): only THIS container funds itself, so a
# player cannot reach another player's faucet to get gas on their chain.

# ---------------------------------------------------------- client config
# Deterministic, non-interactive client setup pointing at the local node.
echo "[]" > "$CFG/sui.keystore"
cat > "$CFG/client.yaml" <<EOF
keystore:
  File: $CFG/sui.keystore
envs:
  - alias: ctf
    rpc: "http://127.0.0.1:${RPC_PORT}"
    ws: ~
    basic_auth: ~
active_env: ctf
active_address: ~
EOF

rpc_call() { # $1 method, $2 params-json
  curl -s -X POST "http://127.0.0.1:${RPC_PORT}" \
    -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$1\",\"params\":$2}"
}

log "waiting for RPC readiness..."
READY=""
for _ in $(seq 1 90); do
  if kill -0 "$SUI_PID" 2>/dev/null; then :; else fail "sui process died during boot; see sui.log"; fi
  if rpc_call sui_getChainIdentifier '[]' | grep -q '"result"'; then READY=1; break; fi
  sleep 1
done
[ -n "$READY" ] || fail "RPC not ready in time"
CHAIN_ID="$(rpc_call sui_getChainIdentifier '[]' | jq -r '.result // empty')"
log "node ready (chain id: $CHAIN_ID)"

# ------------------------------------------------------------- keypairs
sui client new-address ed25519 deployer --json >/dev/null
sui client new-address ed25519 player --json >/dev/null
DEPLOYER="$(sui client addresses --json | jq -r '.addresses[] | select(.[0]=="deployer") | .[1]')"
PLAYER="$(sui client addresses --json | jq -r '.addresses[] | select(.[0]=="player") | .[1]')"
[ -n "$DEPLOYER" ] && [ -n "$PLAYER" ] || fail "failed to create keypairs"
log "deployer=$DEPLOYER player=$PLAYER"

faucet() { # $1 address (best-effort single request)
  curl -s -X POST "http://127.0.0.1:${FAUCET_PORT}/gas" \
    -H 'content-type: application/json' \
    -d "{\"FixedAmountRequest\":{\"recipient\":\"$1\"}}" >/dev/null || true
}
coin_count() { sui client gas "$1" --json 2>/dev/null | jq 'length' 2>/dev/null || echo 0; }
ensure_gas() { # $1 address — keep requesting until gas appears (faucet may lag RPC)
  local t=0
  until [ "$(coin_count "$1")" -gt 0 ] 2>/dev/null; do
    faucet "$1"
    t=$((t + 1)); [ "$t" -gt 40 ] && return 1
    sleep 1
  done
}

log "funding deployer..."
ensure_gas "$DEPLOYER" || fail "deployer never received gas"

# ------------------------------------------------------------- publish
sui client switch --address deployer >/dev/null
log "publishing package: $PACKAGE_DIR"
# `test-publish` recompiles and fetches the Move framework from github, which can
# fail transiently (DNS/network). Retry a few times; after the first attempt the
# framework is cached in ~/.move so retries are fast.
PACKAGE_ID=""
for attempt in 1 2 3 4; do
  sui client test-publish "$PACKAGE_DIR" --build-env ctf --json \
    --skip-dependency-verification > "$OUT/publish.json" 2> "$OUT/publish.err" || true
  PACKAGE_ID="$(jq -r '.objectChanges[]? | select(.type=="published") | .packageId' "$OUT/publish.json" 2>/dev/null || true)"
  if [ -n "$PACKAGE_ID" ] && [ "$PACKAGE_ID" != "null" ]; then break; fi
  PACKAGE_ID=""
  log "publish attempt $attempt failed (transient dependency fetch?); retrying..."
  tail -2 "$OUT/publish.err" >&2 2>/dev/null || true
  sleep 3
done
[ -n "$PACKAGE_ID" ] || { cat "$OUT/publish.err" >&2; fail "publish failed after retries"; }
log "package_id=$PACKAGE_ID"

# accumulate created objects as [{objectId, type}]
ALL_OBJECTS="$(jq -c '[.objectChanges[]? | select(.type=="created") | {objectId, type: .objectType}]' "$OUT/publish.json")"
collect() { # $1 tx-json-file
  local more; more="$(jq -c '[.objectChanges[]? | select(.type=="created") | {objectId, type: .objectType}]' "$1")"
  ALL_OBJECTS="$(jq -cn --argjson a "$ALL_OBJECTS" --argjson b "$more" '$a + $b')"
}

# ------------------------------------------------------------- deploy steps
STEPS="$(echo "$DEPLOY_SPEC" | jq 'length')"
log "running $STEPS deploy step(s)"
for i in $(seq 0 $((STEPS - 1))); do
  m="$(echo "$DEPLOY_SPEC" | jq -r ".[$i].module")"
  f="$(echo "$DEPLOY_SPEC" | jq -r ".[$i].function")"
  mapfile -t ARGS  < <(echo "$DEPLOY_SPEC" | jq -r ".[$i].args[]?")
  mapfile -t TARGS < <(echo "$DEPLOY_SPEC" | jq -r ".[$i].type_args[]?")

  CALL=(sui client call --package "$PACKAGE_ID" --module "$m" --function "$f" --json --gas-budget "$GAS_BUDGET")
  [ "${#TARGS[@]}" -gt 0 ] && CALL+=(--type-args "${TARGS[@]}")
  [ "${#ARGS[@]}"  -gt 0 ] && CALL+=(--args "${ARGS[@]}")
  log "deploy[$i]: ${m}::${f}"
  "${CALL[@]}" > "$OUT/deploy_$i.json" 2> "$OUT/deploy_$i.err" \
    || { cat "$OUT/deploy_$i.err" >&2; fail "deploy step $i (${m}::${f}) failed"; }
  collect "$OUT/deploy_$i.json"
done

# ------------------------------------------------------------- fund player
log "funding player ($PLAYER_FAUCET_COINS grant(s))..."
ensure_gas "$PLAYER" || fail "player never received gas"
# extra grants for generosity (each grant = several 200-SUI coins)
for _ in $(seq 1 "$PLAYER_FAUCET_COINS"); do faucet "$PLAYER"; sleep 1; done

PLAYER_KEY="$(sui keytool export --key-identity player --json | jq -r '.exportedPrivateKey')"
[ -n "$PLAYER_KEY" ] && [ "$PLAYER_KEY" != "null" ] || fail "failed to export player key"

# ------------------------------------------------------------- ready.json
jq -n \
  --arg pkg "$PACKAGE_ID" \
  --arg player "$PLAYER" \
  --arg pkey "$PLAYER_KEY" \
  --arg deployer "$DEPLOYER" \
  --arg chain "$CHAIN_ID" \
  --argjson rpc_port "$RPC_PORT" \
  --argjson objects "$ALL_OBJECTS" \
  '{package_id:$pkg, player_address:$player, player_private_key:$pkey,
    deployer_address:$deployer, chain_id:$chain, rpc_port:$rpc_port,
    objects:$objects}' > "$OUT/ready.json.tmp"
mv "$OUT/ready.json.tmp" "$OUT/ready.json"
log "instance ready — wrote $OUT/ready.json"

# ------------------------------------------------------------- stay alive
wait "$SUI_PID"
