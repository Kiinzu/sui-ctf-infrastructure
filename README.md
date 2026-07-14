# sui-ctf-infrastructure

Per-player, fully-isolated **Sui Move** CTF instances: a Paradigm-CTF-style
launcher targeting Sui instead of EVM. Each player gets their **own** throwaway
`sui start` network (own genesis, own object IDs) with the challenge pre-deployed
and a funded keypair. Players interact over a classic `nc` menu, and the flag is
only released after a server-side solve-check.

```
                    docker compose up
                          │
              ┌───────────▼─────────────┐        spawns (docker.sock)
   nc :1337   │      orchestrator        │─────────────┐
 ────────────▶│  nc menu + rpc proxy +   │             ▼
   player     │  reaper + registry       │      ┌───────────────┐   ┌───────────────┐
              │                          │      │ sui-inst-abc  │   │ sui-inst-def  │  ...
 rpc :8080    │  http://host:8080/<uuid> │─────▶│ sui start +   │   │ sui start +   │
 ────────────▶│      (path-routed proxy) │      │ challenge pkg │   │ challenge pkg │
   player     └──────────────────────────┘      └───────────────┘   └───────────────┘
                                                  one throwaway network per player
```

- **Isolation by construction**: one container + one `sui start` genesis per
  player. No shared network, no cross-player interference.
- **nc-only** player interface (spawn / kill / flag) with a **proof-of-work**
  gate and **1 live instance per source IP**.
- **15-minute TTL** with a background reaper (no orphaned containers).
- Flag never touches the wire from RPC alone. Only the backend releases it, after
  the solve-check passes.

---

## Quickstart

Requirements on the host: Docker + Docker Compose, and outbound internet (to pull
base images and the pinned Sui release).

```bash
cp .env.example .env          # edit PUBLIC_HOST to your public IP/DNS
make build                    # builds orchestrator + instance images (slow once)
make up                       # starts the orchestrator

# play:
nc <host> 1337
#   1 - spawn instance   -> solve PoW -> get uuid / rpc_url / private_key / ids
#   2 - kill instance
#   3 - flag             -> paste uuid -> flag if solved
```

`make ps` shows the orchestrator + live instances. `make down` stops the
orchestrator; `make clean` removes any leftover instance containers.

### Try the placeholder challenge

```bash
nc <host> 1337    # option 1, solve PoW, note rpc_url / private_key / package_id / pool id
cd challenges/placeholder-flashpool/solve
RPC_URL="http://<host>:8080/<uuid>" PRIVATE_KEY="suiprivkey1..." \
  PACKAGE_ID="0x..." POOL_ID="0x..." ./solve.sh
nc <host> 1337    # option 3, paste uuid -> flag
```

---

## The nc flow

On connect the menu is printed:

```
1 - spawn instance
2 - kill instance
3 - flag
>>
```

- **spawn**: prints a proof-of-work puzzle + a copy-paste solver one-liner. On a
  correct nonce, and if your IP has no live instance, the orchestrator launches a
  container, boots the Sui network, publishes the challenge, seeds it, funds a
  fresh player keypair, and prints `uuid`, `rpc_url`, `private_key`,
  `package_id`, the challenge object IDs, and the expiry.
- **kill**: destroys your instance early (keyed by the uuid you paste).
- **flag**: runs the challenge solve-check against your instance, and returns the
  flag only if the win condition is met.

The `uuid` is your bearer credential, so keep it (and keep the `rpc_url` private:
anyone who has either can read/drain/claim/destroy your instance). Instances
auto-destroy after the TTL regardless.

### Client tooling: use an SDK / JSON-RPC, not the `sui` CLI

`rpc_url` (`http://host:8080/<uuid>`) is a **JSON-RPC** endpoint behind the path
proxy. The **TS SDK (`@mysten/sui`)**, **pysui**, and raw JSON-RPC all work through
it (build/sign/execute verified end-to-end). The **`sui` CLI does NOT**: since
Sui 1.74 its transaction transport is **gRPC over HTTP/2**, and gRPC can't be
carried by a URL-path-routing proxy (the path is the gRPC method name), so
`sui client call` against `rpc_url` fails with an `h2 protocol error`. The bundled
`solve.sh` uses JSON-RPC for exactly this reason. If you specifically need CLI
access, an operator must expose a per-instance host port (see below); the path
proxy is JSON-RPC/SDK only.

---

## Security model

| Concern | Mechanism |
|---|---|
| Instance isolation | one container + `--force-regenesis` per player |
| Spawn abuse | proof-of-work gate (`POW_DIFFICULTY_BITS`) |
| Hoarding | 1 live instance per source IP (`MAX_INSTANCES_PER_IP`) |
| Global capacity | `MAX_TOTAL_INSTANCES` |
| RPC exposure | reachable **only** via `/<uuid>` proxy for a live instance; unknown/expired uuid returns 404. Instances publish no host ports. |
| Flag disclosure | released only by the backend after the solve-check; the flag is **never** passed to the instance or placed on-chain (on-chain state is publicly RPC-readable) |
| Cross-player gas | each instance's faucet binds `127.0.0.1`, so a player can't reach another player's faucet to fund an address on their chain |
| Container hardening | `cap_drop: ALL`, `no-new-privileges`, `pids_limit`; `challenge.yml` (flags) + solver stripped from the instance image |
| Flood control | proxy per-uuid + global in-flight caps (429); nc per-IP + global connection caps |
| Resource caps | per-instance `INSTANCE_MEM` (>=2g) + `INSTANCE_NANO_CPUS` |
| Lifetime | 15-min TTL + reaper; registry reconciles against `docker ps` on restart |

> **Residual hardening (optional):** instances share one bridge network, so a
> player *can* reach another instance's RPC port (read-only public chain data;
> they can't act on it without gas, which the localhost-bound faucet denies). For
> full network isolation, give each instance its own Docker network. Also run the
> nc listener where it sees **real client IPs** (see the deployment caveat) so the
> per-IP cap is meaningful.

---

## Configuration (`.env`)

| Var | Default | Notes |
|---|---|---|
| `PUBLIC_HOST` | `localhost` | Host in the player's `rpc_url`. **Set this.** |
| `NC_PORT` | `1337` | Public nc menu port (`nc <host> $NC_PORT`) |
| `PROXY_PORT` | `8080` | Public RPC proxy port (`http://<host>:$PROXY_PORT/<uuid>`) |
| `CHALLENGE` | `placeholder-flashpool` | Which dir under `challenges/` to serve |
| `SUI_VERSION` | `mainnet-v1.74.1` | Pinned into the instance image |
| `INSTANCE_TTL_SECONDS` | `900` | 15 minutes |
| `MAX_INSTANCES_PER_IP` | `1` | |
| `MAX_TOTAL_INSTANCES` | `20` | worst-case RAM = this x `INSTANCE_MEM` |
| `INSTANCE_MEM` | `2g` | per-instance memory cap (`sui start` OOMs at 1g) |
| `INSTANCE_NANO_CPUS` | `1000000000` | 1.0 CPU |
| `POW_DIFFICULTY_BITS` | `20` | leading zero bits; `0` disables |
| `INSTANCE_BOOT_TIMEOUT` | `360` | seconds to boot+deploy an instance |
| `DOCKER_SOCK` | `/var/run/docker.sock` | host Docker socket (Colima differs) |
| `INSTANCE_DNS` | `8.8.8.8,1.1.1.1` | DNS for instances (framework fetch needs it) |

> **Instances need outbound internet at spawn.** `sui client test-publish` fetches
> the Move framework from github when publishing the challenge (Sui >=1.74 has no
> offline/skip-fetch flag). `INSTANCE_DNS` is set to public resolvers by default
> because some VM/Colima container resolvers can't resolve `github.com`.

---

## Adding / swapping a challenge

A challenge is a directory under `challenges/` with a Move `package/` and a
`challenge.yml`:

```yaml
name: my-challenge
description: "..."
flag: "flag{...}"
sui_version: "mainnet-v1.74.1"     # match the instance image
ttl_seconds: 900
package_path: "package"             # Move package to publish
player_faucet_coins: 3
deploy:                             # `sui client call`s run after publish
  - module: "pool"
    function: "init_pool"
    type_args: []
    args: ["1000000"]
solve:                              # win condition evaluated by /flag
  type: state                       # or: event
  object_type: "::pool::Pool"       # suffix-matched vs created object types
  field: "balance"
  op: "eq"                          # eq|ne|lt|lte|gt|gte
  value: 0
flag_mode: server_issued            # or: on_chain_claim
```

- **`solve.type: state`** reads a created object's field over RPC and compares it.
- **`solve.type: event`** checks whether a Move event `<package>::<event_type>`
  was emitted (`event_type: "::pool::FlagClaimed"`). The emitter is bound to the
  **player by default** (`sender: player`); use `sender: any` to disable. Pair
  with `flag_mode: on_chain_claim` so the win is *proven on-chain* by the
  player's exploit. Both flag modes are supported per-challenge.

> **Never put the flag on-chain.** On Sui, all on-chain state (object fields
> **and** transaction inputs) is publicly readable via RPC (`sui_getObject`,
> `sui_getTransactionBlock`), so a flag embedded on-chain is trivially dumped
> pre-solve. Both flag modes keep the flag **server-side**: the backend runs the
> solve-check (`state` or `event`) and only then returns `flag` from
> `challenge.yml`. `on_chain_claim` differs from `server_issued` only in that the
> win condition is an on-chain *event the player causes* rather than a state read.
> The flag itself still never touches the chain.

After editing the Move package, rebuild the instance image
(`make build-instance`); the package is baked in and published from inside each
instance. Editing only `deploy`/`solve`/`flag`/limits needs just a
`make down && make up` (the orchestrator reads `challenge.yml` live).

---

## Testing

Two layers, both runnable **without Docker** (they use a local `sui` CLI /
Python):

```bash
make test         # 23 pure-Python unit tests (PoW, registry, solve-check, proxy, orchestration)
make local-test   # boots a real localnet and runs spawn->publish->seed->solve->solve-check end to end
```

`make local-test` requires the `sui` CLI, `jq`, `curl`, and `python3` on the host
and validates the exact deploy + solve-check logic the containers use.

---

## Layout

```
orchestrator/           FastAPI proxy + nc menu + reaper + registry (Python)
  app/{config,pow,registry,sui_rpc,challenge,docker_manager,instances,proxy,ncserver,reaper,main}.py
instance-image/         per-player Sui image: Dockerfile + entrypoint.sh
challenges/
  placeholder-flashpool/{challenge.yml, package/, solve/}
scripts/                local_pipeline_test.sh + solvecheck_probe.py
tests/                  pytest unit tests
docker-compose.yml  Makefile  .env.example
```

---

## Troubleshooting

- **Colima / Docker Desktop socket**: the bind-mount source is resolved *inside*
  the VM, so use the in-VM path `DOCKER_SOCK=/var/run/docker.sock` (the default),
  **not** the host-side `~/.colima/...` socket (that fails with "operation not
  supported"). The instance base image is `ubuntu:24.04` to match the glibc the
  Sui release binaries require.
- **Instance image build fails on arch**: the image downloads the Sui release
  for `amd64`/`arm64`; ensure `SUI_VERSION` has Linux release assets
  (`ubuntu-x86_64` / `ubuntu-aarch64`).
- **Spawns time out**: raise `INSTANCE_BOOT_TIMEOUT`; a full `sui start` +
  publish is heavier than Anvil (~30-90s). Check `docker logs sui-inst-<id>`.
- **RAM pressure**: `MAX_TOTAL_INSTANCES x INSTANCE_MEM` is the worst case; lower
  the cap or the per-instance memory for a smaller host.
- **PoW too slow/fast**: tune `POW_DIFFICULTY_BITS` (each +1 bit is ~2x work);
  `0` disables it for local testing.
- **`sui client` fails through `rpc_url` (`h2 protocol error`)**: expected. The
  path proxy is JSON-RPC only; the CLI needs gRPC. Use an SDK/JSON-RPC. To offer
  CLI access, expose a per-instance host port instead of (or alongside) the proxy
  by publishing the instance's `9000` with a host port range in
  `docker_manager.run_instance` and returning that `host:port` as the rpc_url.
  Note this reveals the RPC directly (the flag stays gated by the solve-check
  regardless).

## Deployment caveat: real client IPs

The per-IP cap is only as good as the source IP the nc listener sees. Behind
Docker's userland proxy (e.g. Colima, or `ports:` publishing) **all connections
appear to come from the bridge gateway** (`172.x.0.1`), which would collapse the
cap to one instance for the whole server. For a real event, make sure the
orchestrator sees real client IPs: run the nc listener with host networking, or
front it with a TCP load balancer that preserves the source IP / speaks the PROXY
protocol. (For strict per-team limits regardless of IP, gate spawns on a CTFd
team ticket instead; the codebase is structured to add that.)

## Notes on what's validated

Built and validated **live end-to-end** against `sui` **1.74.1** through Docker.
A real `nc` spawn boots an isolated Sui network in a container, publishes + seeds
the challenge, funds a player key, and returns the proxy `rpc_url`; the bundled
JSON-RPC solver drains the pool through the `/uuid` proxy; and option 3 releases
the flag **only after** the solve-check passes. Verified: the flag is gated before
solving, and a same-IP second spawn is refused **without leaking credentials**. 23
unit tests cover PoW, registry, per-IP caps, spawn/kill orchestration, solve-checks
(state + event), and proxy routing; `make local-test` covers the challenge
pipeline end-to-end without Docker.

Not yet exercised live: 20-way concurrent load. Isolation is by construction (one
container + `--force-regenesis` per player, with a distinct genesis and objects
each).
