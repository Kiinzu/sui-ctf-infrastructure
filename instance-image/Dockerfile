# syntax=docker/dockerfile:1
#
# Per-player Sui instance image: a pinned Sui CLI + the challenge package, run as
# a throwaway `sui start` network. Build context is the REPO ROOT (so it can COPY
# ./challenges); build via docker-compose (service `sui-instance`).
#
# NOTE: in Sui 1.74.1 the fullnode RPC binds 0.0.0.0 by default, so NO socat/port
# shim is needed — the orchestrator reaches it by container name over the
# internal Docker network.
#
# Base = ubuntu:24.04 (glibc 2.39 / libstdc++ GLIBCXX_3.4.32) to match the
# `ubuntu-*` Sui release binaries. debian:bookworm (glibc 2.36) is too old and
# fails with "GLIBC_2.38 not found".
FROM ubuntu:24.04

ARG SUI_VERSION=mainnet-v1.74.1
# Set automatically by BuildKit from the build platform (amd64 / arm64).
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl jq bash git libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# --- install pinned Sui release binaries for the target architecture ---------
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) A="ubuntu-x86_64" ;; \
      arm64) A="ubuntu-aarch64" ;; \
      *) echo "unsupported TARGETARCH='${TARGETARCH}'"; exit 1 ;; \
    esac; \
    url="https://github.com/MystenLabs/sui/releases/download/${SUI_VERSION}/sui-${SUI_VERSION}-${A}.tgz"; \
    echo "downloading ${url}"; \
    mkdir -p /opt/sui; \
    curl -fsSL "${url}" | tar -xz -C /opt/sui; \
    find /opt/sui -type f \( -name 'sui' -o -name 'sui-*' \) -exec cp {} /usr/local/bin/ \; ; \
    chmod +x /usr/local/bin/sui*; \
    rm -rf /opt/sui; \
    sui --version

# --- bake in the challenge PACKAGES (published from inside the instance) ---
COPY challenges /challenges
# The instance only needs the Move packages. Strip challenge.yml (which holds the
# server_issued flag) and solve/ dirs so nothing secret sits in a player-reachable
# image — the orchestrator reads challenge.yml from its OWN mount, not from here.
RUN find /challenges -name 'challenge.yml' -delete \
    && find /challenges -type d -name 'solve' -exec rm -rf {} + 2>/dev/null || true

# A minimal client config so `sui move build` below doesn't hit the interactive
# first-run prompt. The entrypoint rewrites this at runtime anyway.
RUN mkdir -p /root/.sui/sui_config \
    && echo '[]' > /root/.sui/sui_config/sui.keystore \
    && printf 'keystore:\n  File: /root/.sui/sui_config/sui.keystore\nenvs:\n  - alias: ctf\n    rpc: "http://127.0.0.1:9000"\n    ws: ~\n    basic_auth: ~\nactive_env: ctf\nactive_address: ~\n' \
       > /root/.sui/sui_config/client.yaml

# Pre-warm the Move dependency cache (fetches the framework once at build time),
# so runtime publishes are fast. Best-effort — don't fail the build on it.
ARG PREBUILD_CHALLENGE=placeholder-flashpool
RUN set -eux; \
    if [ -d "/challenges/${PREBUILD_CHALLENGE}/package" ]; then \
      ( cd "/challenges/${PREBUILD_CHALLENGE}/package" && sui move build ) || true; \
    fi

COPY instance-image/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Fullnode RPC — reached by the orchestrator over the internal Docker network.
EXPOSE 9000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
