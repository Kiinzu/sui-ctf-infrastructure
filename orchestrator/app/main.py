"""Entrypoint: runs the nc menu, the RPC proxy, and the reaper in one loop."""
from __future__ import annotations

import asyncio
import logging

import uvicorn

from .challenge import load_challenge
from .config import CONFIG
from .docker_manager import DockerManager
from .instances import InstanceManager
from .ncserver import NcServer
from .proxy import create_proxy_app
from .reaper import Reaper
from .registry import Registry

log = logging.getLogger("suictf.main")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def _wait_for_docker(docker: DockerManager, attempts: int = 15) -> bool:
    for i in range(attempts):
        try:
            await asyncio.to_thread(docker.ping)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("docker not reachable yet (%s/%s): %s", i + 1, attempts, exc)
            await asyncio.sleep(2)
    return False


async def main() -> None:
    _setup_logging()
    cfg = CONFIG

    challenge = load_challenge(cfg.challenges_dir, cfg.active_challenge)
    log.info("active challenge: %s (flag_mode=%s)", challenge.name, challenge.flag_mode)

    registry = Registry(cfg.db_path)
    docker = DockerManager(
        image=cfg.instance_image,
        network=cfg.docker_network,
        mem_limit=cfg.instance_mem,
        nano_cpus=cfg.instance_nano_cpus,
        rpc_port=cfg.instance_rpc_port,
        dns=cfg.dns_list,
    )

    if not await _wait_for_docker(docker):
        raise SystemExit("Docker daemon not reachable — is /var/run/docker.sock mounted?")
    if not docker.image_exists():
        log.warning(
            "instance image %r not found — build it before players spawn "
            "(docker compose build sui-instance)", cfg.instance_image,
        )

    manager = InstanceManager(
        config=cfg, registry=registry, docker=docker, challenge=challenge,
    )
    await manager.reconcile_startup()

    reaper = Reaper(config=cfg, registry=registry, docker=docker)
    reaper.start()

    proxy_app = create_proxy_app(cfg, registry)
    server = uvicorn.Server(uvicorn.Config(
        proxy_app, host="0.0.0.0", port=cfg.proxy_port,
        log_level="warning", access_log=False,
    ))

    nc = NcServer(config=cfg, manager=manager)
    await nc.start()

    log.info(
        "orchestrator up: nc=:%s  rpc-proxy=:%s  ttl=%ss  pow_bits=%s  max=%s",
        cfg.nc_port, cfg.proxy_port,
        challenge.ttl_seconds or cfg.ttl_seconds,
        cfg.pow_difficulty_bits, cfg.max_total_instances,
    )
    await asyncio.gather(server.serve(), nc.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
