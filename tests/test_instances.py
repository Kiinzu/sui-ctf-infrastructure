import asyncio
import dataclasses

from conftest import CHALLENGES_DIR

from app.challenge import load_challenge
from app.config import Config
from app.instances import InstanceManager, SpawnError
from app.registry import Registry

READY = {
    "package_id": "0xpkg",
    "player_private_key": "suiprivkey1xxxx",
    "player_address": "0xplayer",
    "deployer_address": "0xdeployer",
    "objects": [{"objectId": "0xpool", "type": "0xpkg::pool::Pool"}],
}


class FakeDocker:
    def __init__(self, ready):
        self.ready = ready
        self.removed = []

    def run_instance(self, *, uuid, container_name, environment):
        return "cid-" + uuid

    def read_ready(self, container_id):
        return self.ready

    def container_status(self, container_id):
        return "running"

    def get_logs(self, container_id, tail=80):
        return "(logs)"

    def stop_and_remove(self, *, container_id=None, container_name=None):
        self.removed.append(container_id or container_name)

    def list_managed(self):
        return []


def _mgr(tmp_path, ready=READY, **cfg_over):
    cfg_over.setdefault("instance_boot_timeout", 5)
    cfg = dataclasses.replace(
        Config(),
        public_host="testhost",
        public_proxy_port=8080,
        db_path=str(tmp_path / "r.sqlite"),
        challenges_dir=CHALLENGES_DIR,
        active_challenge="placeholder-flashpool",
        **cfg_over,
    )
    reg = Registry(cfg.db_path)
    ch = load_challenge(CHALLENGES_DIR, "placeholder-flashpool")
    docker = FakeDocker(ready)
    return InstanceManager(config=cfg, registry=reg, docker=docker, challenge=ch), reg, docker


def test_spawn_returns_player_view(tmp_path):
    mgr, reg, _ = _mgr(tmp_path)
    res = asyncio.run(mgr.spawn("1.2.3.4"))
    assert res["package_id"] == "0xpkg"
    assert res["private_key"] == "suiprivkey1xxxx"
    assert res["rpc_url"].startswith("http://testhost:8080/")
    assert res["rpc_url"].endswith(res["uuid"])
    assert res["objects"][0]["objectId"] == "0xpool"
    assert "already_existed" not in res
    assert reg.get(res["uuid"])["status"] == "running"


def test_per_ip_cap_refuses_without_leaking(tmp_path):
    # A second spawn from the same IP must be REFUSED and must never disclose the
    # first instance's uuid or private key (the shared-NAT credential-leak bug).
    mgr, _, _ = _mgr(tmp_path)

    async def scenario():
        r1 = await mgr.spawn("1.2.3.4")
        try:
            await mgr.spawn("1.2.3.4")
            return r1, "no-error"
        except SpawnError as exc:
            return r1, str(exc)

    r1, second = asyncio.run(scenario())
    assert second != "no-error"
    assert r1["uuid"] not in second
    assert r1["private_key"] not in second


def test_capacity_rejects(tmp_path):
    mgr, _, _ = _mgr(tmp_path, max_total_instances=1, max_instances_per_ip=5)

    async def scenario():
        await mgr.spawn("1.1.1.1")
        try:
            await mgr.spawn("2.2.2.2")
            return "no-error"
        except SpawnError:
            return "rejected"

    assert asyncio.run(scenario()) == "rejected"


def test_boot_timeout_cleans_up(tmp_path):
    # read_ready never returns a payload -> boot times out -> container removed.
    mgr, reg, docker = _mgr(tmp_path, ready=None, instance_boot_timeout=1)

    async def scenario():
        try:
            await mgr.spawn("3.3.3.3")
            return "no-error"
        except SpawnError:
            return "rejected"

    assert asyncio.run(scenario()) == "rejected"
    assert docker.removed, "container should be cleaned up after failed boot"


def test_kill(tmp_path):
    mgr, reg, docker = _mgr(tmp_path)

    async def scenario():
        r = await mgr.spawn("4.4.4.4")
        ok = await mgr.kill(r["uuid"])
        return r["uuid"], ok

    uuid, ok = asyncio.run(scenario())
    assert ok is True
    assert reg.get(uuid)["status"] == "killed"
    assert docker.removed
