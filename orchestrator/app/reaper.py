"""Background TTL reaper: kills + cleans up instances past their expiry."""
from __future__ import annotations

import asyncio
import logging
import time

from .config import Config
from .docker_manager import DockerManager
from .registry import Registry

log = logging.getLogger("suictf.reaper")


class Reaper:
    def __init__(self, *, config: Config, registry: Registry, docker: DockerManager):
        self.cfg = config
        self.reg = registry
        self.docker = docker
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="reaper")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        log.info("reaper started (interval=%ss)", self.cfg.reaper_interval)
        while True:
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001 - never let the reaper die
                log.exception("reaper sweep failed")
            await asyncio.sleep(self.cfg.reaper_interval)

    async def _sweep(self) -> None:
        now = time.time()
        for rec in self.reg.due_for_reap(now):
            # Never reap a container still inside its boot window — protects a
            # challenge whose ttl_seconds is shorter than the boot time from
            # having its container yanked mid-deploy.
            if (rec["status"] == "starting"
                    and (now - rec["created_at"]) < self.cfg.instance_boot_timeout):
                continue
            log.info("reaping expired instance %s", rec["uuid"])
            await asyncio.to_thread(
                self.docker.stop_and_remove,
                container_id=rec.get("container_id"),
                container_name=rec.get("container_name"),
            )
            self.reg.set_status(rec["uuid"], "expired")
