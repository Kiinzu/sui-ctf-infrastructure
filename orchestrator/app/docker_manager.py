"""Thin wrapper over the Docker SDK for per-instance Sui containers.

The orchestrator holds the Docker socket and launches sibling containers
("docker-out-of-docker"): one throwaway `sui start` network per player. Instances
publish NO host ports — the RPC is reached only via the orchestrator's internal
network and the `/uuid` proxy.

All methods here are blocking; callers run them via `asyncio.to_thread`.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import docker
from docker.errors import DockerException, NotFound

LABEL_KEY = "suictf"
READY_PATH = "/instance/ready.json"


class DockerManager:
    def __init__(self, *, image: str, network: str, mem_limit: str,
                 nano_cpus: int, rpc_port: int, dns: Optional[list[str]] = None):
        self.image = image
        self.network = network
        self.mem_limit = mem_limit
        self.nano_cpus = nano_cpus
        self.rpc_port = rpc_port
        self.dns = dns or None
        self.client = docker.from_env()

    def ping(self) -> None:
        self.client.ping()

    def image_exists(self, image: Optional[str] = None) -> bool:
        try:
            self.client.images.get(image or self.image)
            return True
        except (NotFound, DockerException):
            return False

    def run_instance(self, *, uuid: str, container_name: str,
                     environment: dict[str, str]) -> str:
        """Launch a new instance container detached; return its container id."""
        container = self.client.containers.run(
            self.image,
            name=container_name,
            detach=True,
            environment=environment,
            network=self.network,
            mem_limit=self.mem_limit,
            nano_cpus=self.nano_cpus,
            labels={LABEL_KEY: "1", f"{LABEL_KEY}.uuid": uuid},
            restart_policy={"Name": "no"},
            dns=self.dns,  # reliable resolver for the framework git fetch at deploy
            # Hardening: sui needs no Linux capabilities (binds >1024, writes only
            # its own files); block privilege escalation and cap task count to
            # backstop fork-bombs (generous so sui's threads aren't starved).
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            pids_limit=2048,
            # No `ports=` mapping on purpose: RPC stays internal.
        )
        return container.id

    def read_ready(self, container_id: str) -> Optional[dict[str, Any]]:
        """Return the instance's ready.json once fully written, else None."""
        try:
            container = self.client.containers.get(container_id)
        except NotFound:
            return None
        try:
            code, output = container.exec_run(["cat", READY_PATH])
        except DockerException:
            return None
        if code != 0 or not output:
            return None
        try:
            return json.loads(output.decode())
        except (ValueError, UnicodeDecodeError):
            # File may be mid-write; the entrypoint writes atomically so this
            # should only be a transient race — treat as not-ready-yet.
            return None

    def container_status(self, container_id: str) -> Optional[str]:
        try:
            return self.client.containers.get(container_id).status
        except NotFound:
            return None
        except DockerException:
            return None

    def get_logs(self, container_id: str, tail: int = 60) -> str:
        try:
            container = self.client.containers.get(container_id)
            return container.logs(tail=tail).decode(errors="replace")
        except (NotFound, DockerException):
            return ""

    def stop_and_remove(self, *, container_id: Optional[str] = None,
                        container_name: Optional[str] = None) -> None:
        ref = container_id or container_name
        if not ref:
            return
        try:
            container = self.client.containers.get(ref)
        except NotFound:
            return
        except DockerException:
            return
        try:
            container.remove(force=True)  # force = stop + remove in one call
        except (NotFound, DockerException):
            pass

    def list_managed(self) -> list[dict[str, Any]]:
        """All containers we own (by label), for startup reconciliation."""
        try:
            containers = self.client.containers.list(
                all=True, filters={"label": LABEL_KEY}
            )
        except DockerException:
            return []
        out = []
        for c in containers:
            out.append({
                "id": c.id,
                "name": c.name,
                "uuid": c.labels.get(f"{LABEL_KEY}.uuid"),
                "status": c.status,
            })
        return out
