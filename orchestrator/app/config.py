"""Environment-driven configuration for the orchestrator.

Every knob has a sensible default so `docker compose up` works out of the box;
override via the environment (see .env.example).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _str(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Config:
    # --- networking / exposure ---------------------------------------------
    nc_port: int = _int("NC_PORT", 1337)
    proxy_port: int = _int("PROXY_PORT", 8080)
    # Host:port players see in their rpc_url. Set to your public DNS/IP in prod.
    public_host: str = _str("PUBLIC_HOST", "localhost")
    # If the proxy is reached on a different public port (e.g. behind another
    # LB), advertise that here; defaults to proxy_port.
    public_proxy_port: int = _int("PUBLIC_PROXY_PORT", _int("PROXY_PORT", 8080))

    # --- instance containers ------------------------------------------------
    instance_image: str = _str("INSTANCE_IMAGE", "sui-ctf-instance:latest")
    docker_network: str = _str("DOCKER_NETWORK", "suictf_net")
    instance_name_prefix: str = _str("INSTANCE_NAME_PREFIX", "sui-inst")
    instance_rpc_port: int = _int("INSTANCE_RPC_PORT", 9000)
    # Per-instance resource caps. `sui start` (validator+fullnode+faucet) peaks
    # above 1g at genesis and gets OOM-killed at a 1g cap, so 2g is the floor.
    # 20 * 2g = 40g RAM at full load — size the host or lower MAX_TOTAL_INSTANCES.
    instance_mem: str = _str("INSTANCE_MEM", "2g")
    instance_nano_cpus: int = _int("INSTANCE_NANO_CPUS", 1_000_000_000)  # 1.0 CPU
    # Seconds to wait for a fresh instance to boot + deploy + fund. Must exceed
    # the instance entrypoint's own internal retry budget (RPC-ready + funding +
    # publish + deploy) with headroom, else healthy-but-slow instances get reaped.
    instance_boot_timeout: int = _int("INSTANCE_BOOT_TIMEOUT", 360)
    # DNS servers for instance containers. `sui client (test-)publish` fetches the
    # Move framework from github at deploy time, so instances need working DNS;
    # some VM/Colima default resolvers are flaky. Comma-separated; empty = docker
    # default.
    instance_dns: str = _str("INSTANCE_DNS", "8.8.8.8,1.1.1.1")

    # --- lifecycle / limits -------------------------------------------------
    ttl_seconds: int = _int("INSTANCE_TTL_SECONDS", 900)  # 15 min default
    max_instances_per_ip: int = _int("MAX_INSTANCES_PER_IP", 1)
    max_total_instances: int = _int("MAX_TOTAL_INSTANCES", 20)
    reaper_interval: int = _int("REAPER_INTERVAL", 15)

    # --- proof of work ------------------------------------------------------
    # Leading zero *bits* required. 0 disables PoW (handy for local testing).
    pow_difficulty_bits: int = _int("POW_DIFFICULTY_BITS", 20)
    pow_timeout: int = _int("POW_TIMEOUT", 180)  # seconds player has to answer

    # --- challenge ----------------------------------------------------------
    challenges_dir: str = _str("CHALLENGES_DIR", "/challenges")
    active_challenge: str = _str("CHALLENGE", "placeholder-flashpool")

    # --- storage ------------------------------------------------------------
    db_path: str = _str("DB_PATH", "/data/registry.sqlite")

    # --- nc UX --------------------------------------------------------------
    nc_read_timeout: int = _int("NC_READ_TIMEOUT", 300)  # idle disconnect

    @property
    def dns_list(self) -> list[str]:
        return [s.strip() for s in self.instance_dns.split(",") if s.strip()]

    def rpc_url_for(self, uuid: str) -> str:
        return f"http://{self.public_host}:{self.public_proxy_port}/{uuid}"

    def container_name(self, uuid: str) -> str:
        # Full uuid — DNS-safe and collision-free (docker allows long names), so a
        # name clash can never make one player's spawn touch another's container.
        return f"{self.instance_name_prefix}-{uuid}"


CONFIG = Config()
