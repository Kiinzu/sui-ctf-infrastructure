"""Instance lifecycle orchestration.

Ties together the registry, Docker manager, challenge manifest and RPC client to
implement spawn / kill / flag / status, plus startup reconciliation. All Docker
calls (blocking) are dispatched via asyncio.to_thread so the event loop that also
serves nc + the RPC proxy never blocks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid as uuidlib
from typing import Any, Optional

from .challenge import Challenge, check_solved
from .config import Config
from .docker_manager import DockerManager
from .registry import Registry
from .sui_rpc import SuiRpc

log = logging.getLogger("suictf.instances")


class SpawnError(Exception):
    """Raised when a spawn cannot proceed; message is player-safe."""


class InstanceManager:
    def __init__(self, *, config: Config, registry: Registry,
                 docker: DockerManager, challenge: Challenge):
        self.cfg = config
        self.reg = registry
        self.docker = docker
        self.ch = challenge
        self._reserve_lock = asyncio.Lock()

    # ------------------------------------------------------------------ spawn
    async def spawn(self, source_ip: str) -> dict[str, Any]:
        # Reserve a slot atomically (fast path) so concurrent spawns from the
        # same IP / at capacity can't both slip through the checks.
        async with self._reserve_lock:
            # IMPORTANT: never return an existing instance's credentials keyed on
            # source IP. The TCP source IP is not proof of ownership (shared NAT,
            # Docker userland proxy, campus/venue gateway), so handing back the
            # uuid + private_key would disclose another player's instance. Enforce
            # the per-IP cap by REFUSING; the creator already holds their uuid.
            if self.reg.active_count_for_ip(source_ip) >= self.cfg.max_instances_per_ip:
                raise SpawnError(
                    f"This source IP already has an active instance (limit "
                    f"{self.cfg.max_instances_per_ip} per IP). Use your existing "
                    "uuid — option 3 for the flag, option 2 to destroy it — or wait "
                    "for it to expire."
                )
            if self.reg.total_active() >= self.cfg.max_total_instances:
                raise SpawnError(
                    "Server is at capacity right now. Try again in a few minutes."
                )
            uuid = str(uuidlib.uuid4())
            name = self.cfg.container_name(uuid)
            ttl = self.ch.ttl_seconds or self.cfg.ttl_seconds
            expires_at = time.time() + ttl
            self.reg.create(
                uuid=uuid, source_ip=source_ip, challenge=self.ch.name,
                container_name=name, expires_at=expires_at,
                flag_mode=self.ch.flag_mode,
            )

        # Slow path (outside the lock): launch + boot + deploy.
        try:
            await self._boot(uuid, name)
        except Exception as exc:  # noqa: BLE001 - convert to player-safe error
            log.exception("spawn failed for %s", uuid)
            self.reg.set_status(uuid, "error", error=str(exc))
            # Only remove the container WE created (by id), never by name — a name
            # collision must never let a failed spawn destroy someone else's box.
            rec = self.reg.get(uuid)
            cid = rec.get("container_id") if rec else None
            if cid:
                await asyncio.to_thread(self.docker.stop_and_remove, container_id=cid)
            # Do not leak internal exception text / container logs to the player;
            # the full detail is logged server-side above.
            raise SpawnError(
                "Instance failed to start. Please try again in a minute."
            ) from exc

        record = self.reg.get(uuid)
        return self._player_view(record)

    async def _boot(self, uuid: str, name: str) -> None:
        env = self._instance_env()
        container_id = await asyncio.to_thread(
            self.docker.run_instance,
            uuid=uuid, container_name=name, environment=env,
        )
        self.reg.set_container(uuid, container_id)

        deadline = time.time() + self.cfg.instance_boot_timeout
        ready: Optional[dict[str, Any]] = None
        while time.time() < deadline:
            ready = await asyncio.to_thread(self.docker.read_ready, container_id)
            if ready and ready.get("package_id") and ready.get("player_private_key"):
                break
            status = await asyncio.to_thread(
                self.docker.container_status, container_id
            )
            if status in ("exited", "dead", None):
                logs = await asyncio.to_thread(self.docker.get_logs, container_id, 80)
                log.error("instance %s exited during boot; last logs:\n%s", uuid, logs)
                raise RuntimeError("container exited during boot")
            await asyncio.sleep(2)

        if not ready or not ready.get("package_id"):
            logs = await asyncio.to_thread(self.docker.get_logs, container_id, 80)
            log.error("instance %s not ready within %ss; last logs:\n%s",
                      uuid, self.cfg.instance_boot_timeout, logs)
            raise TimeoutError("instance not ready in time")

        self.reg.set_running(
            uuid,
            rpc_url=self.cfg.rpc_url_for(uuid),
            player_address=ready.get("player_address", ""),
            player_privkey=ready.get("player_private_key", ""),
            deployer_address=ready.get("deployer_address", ""),
            package_id=ready["package_id"],
            objects=ready.get("objects", []),
        )
        log.info("instance %s ready (pkg=%s)", uuid, ready["package_id"])

    def _instance_env(self) -> dict[str, str]:
        pkg_dir = f"{self.cfg.challenges_dir}/{self.cfg.active_challenge}/{self.ch.package_path}"
        # The flag is NEVER passed to the instance container — on Sui anything
        # on-chain (object fields AND tx inputs) is publicly RPC-readable. The
        # backend issues the flag after the solve-check, for BOTH flag modes.
        return {
            "CHALLENGE": self.ch.name,
            "PACKAGE_DIR": pkg_dir,
            "DEPLOY_SPEC": json.dumps(self.ch.deploy),
            "PLAYER_FAUCET_COINS": str(self.ch.player_faucet_coins),
            "RPC_PORT": str(self.cfg.instance_rpc_port),
        }

    # ------------------------------------------------------------------- kill
    async def kill(self, uuid: str) -> bool:
        record = self.reg.get(uuid)
        if record is None:
            return False
        await asyncio.to_thread(
            self.docker.stop_and_remove,
            container_id=record.get("container_id"),
            container_name=record.get("container_name"),
        )
        if record["status"] in ("starting", "running"):
            self.reg.set_status(uuid, "killed")
        return True

    # ------------------------------------------------------------------- flag
    async def get_flag(self, uuid: str) -> tuple[bool, str]:
        """Returns (solved, flag_or_message)."""
        record = self.reg.get(uuid)
        if record is None:
            return False, "Unknown instance id."
        if record["status"] != "running":
            return False, f"Instance is not running (status: {record['status']})."
        rpc = SuiRpc(self._internal_rpc_url(record))
        try:
            solved = await check_solved(self.ch, rpc, record)
        except Exception as exc:  # noqa: BLE001
            log.warning("solve-check error for %s: %s", uuid, exc)
            return False, f"Could not evaluate solve state: {exc}"
        if solved:
            return True, self.ch.flag
        return False, "Not solved yet — the win condition is not met."

    # ----------------------------------------------------------------- status
    async def status(self, uuid: str) -> Optional[dict[str, Any]]:
        record = self.reg.get(uuid)
        if record is None:
            return None
        remaining = max(0, int(record["expires_at"] - time.time()))
        return {
            "uuid": uuid,
            "status": record["status"],
            "seconds_remaining": remaining,
            "challenge": record["challenge"],
        }

    # ------------------------------------------------------------- reconcile
    async def reconcile_startup(self) -> None:
        """On boot: kill containers with no active registry row, and mark
        registry rows whose container has vanished as errored."""
        managed = await asyncio.to_thread(self.docker.list_managed)
        active = {r["uuid"]: r for r in self.reg.all_active()}
        managed_uuids = set()
        for c in managed:
            uuid = c.get("uuid")
            managed_uuids.add(uuid)
            if uuid not in active:
                log.info("reconcile: removing orphan container %s", c["name"])
                await asyncio.to_thread(
                    self.docker.stop_and_remove, container_id=c["id"]
                )
        for uuid, rec in active.items():
            if uuid not in managed_uuids:
                log.info("reconcile: registry row %s has no container", uuid)
                self.reg.set_status(uuid, "error", error="container missing on restart")

    # ------------------------------------------------------------- helpers
    def _internal_rpc_url(self, record: dict[str, Any]) -> str:
        return f"http://{record['container_name']}:{self.cfg.instance_rpc_port}/"

    def _player_view(self, record: dict[str, Any]) -> dict[str, Any]:
        remaining = max(0, int(record["expires_at"] - time.time()))
        return {
            "uuid": record["uuid"],
            "rpc_url": record["rpc_url"],
            "private_key": record["player_privkey"],
            "player_address": record["player_address"],
            "package_id": record["package_id"],
            "objects": record.get("objects", []),
            "expires_at": int(record["expires_at"]),
            "seconds_remaining": remaining,
            "challenge": record["challenge"],
        }
