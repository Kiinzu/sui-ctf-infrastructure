# Placeholder challenge — solving

This toy challenge exists to prove the infrastructure pipeline. The "vulnerability"
is intentionally trivial: an open `pool::drain` that sets the pool balance to 0.

## Win condition

`Pool.balance == 0` (a shared object seeded to `1000000` at deploy).

## Solve

1. `nc <host> 1337` → option **1** (solve the proof-of-work) → you get:
   `uuid`, `rpc_url`, `private_key`, `package_id`, and the `::pool::Pool` object id.
2. Run the reference solver:
   ```bash
   RPC_URL="http://<host>:8080/<uuid>" \
   PRIVATE_KEY="suiprivkey1..." \
   PACKAGE_ID="0x..." \
   POOL_ID="0x...<pool>" \
   ./solve.sh
   ```
   (or do the equivalent with the TypeScript SDK / `sui client call` yourself).
3. `nc <host> 1337` → option **3** → paste your `uuid` → get the flag.

## Client tooling note (important)

Your `rpc_url` is a **JSON-RPC** endpoint served through the orchestrator's path
proxy. Use the **TS SDK (`@mysten/sui`)**, **pysui**, or raw JSON-RPC — these all
work. The **`sui` CLI's** transaction path uses **gRPC/HTTP2** (Sui ≥1.74), which
does **not** traverse the path proxy, so `sui client call` against `rpc_url` fails
with an `h2 protocol error`. `solve.sh` therefore builds → signs → executes over
JSON-RPC (the SDK approach).

## Doing it with the TS SDK

Point `SuiClient` at `rpc_url`, import the keypair from `private_key`
(`Ed25519Keypair.fromSecretKey(...)`), build a PTB that calls
`<package_id>::pool::drain(<pool_id>)`, and execute it. Same result.
