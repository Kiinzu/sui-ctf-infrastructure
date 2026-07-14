import time

from app.registry import Registry


def _mk(reg, uuid, ip, expires_in=100):
    reg.create(
        uuid=uuid, source_ip=ip, challenge="c", container_name="n-" + uuid,
        expires_at=time.time() + expires_in, flag_mode="server_issued",
    )


def test_create_and_get(tmp_path):
    reg = Registry(str(tmp_path / "r.sqlite"))
    _mk(reg, "u1", "1.2.3.4")
    r = reg.get("u1")
    assert r["status"] == "starting"
    assert r["source_ip"] == "1.2.3.4"
    assert r["objects"] == []


def test_running_fields_roundtrip(tmp_path):
    reg = Registry(str(tmp_path / "r.sqlite"))
    _mk(reg, "u1", "1.2.3.4")
    reg.set_container("u1", "cid1")
    reg.set_running(
        "u1", rpc_url="http://x/u1", player_address="0xp", player_privkey="k",
        deployer_address="0xd", package_id="0xpkg",
        objects=[{"objectId": "0xo", "type": "0xpkg::pool::Pool"}],
    )
    r = reg.get("u1")
    assert r["status"] == "running"
    assert r["package_id"] == "0xpkg"
    assert r["objects"][0]["objectId"] == "0xo"
    assert r["container_id"] == "cid1"


def test_per_ip_cap_counts(tmp_path):
    reg = Registry(str(tmp_path / "r.sqlite"))
    _mk(reg, "u1", "1.2.3.4")
    _mk(reg, "u2", "1.2.3.4")
    _mk(reg, "u3", "9.9.9.9")
    assert reg.active_count_for_ip("1.2.3.4") == 2
    assert reg.active_count_for_ip("9.9.9.9") == 1
    assert reg.total_active() == 3
    assert reg.active_for_ip("1.2.3.4")["source_ip"] == "1.2.3.4"


def test_reaping(tmp_path):
    reg = Registry(str(tmp_path / "r.sqlite"))
    _mk(reg, "live", "1.1.1.1", expires_in=1000)
    _mk(reg, "dead", "2.2.2.2", expires_in=-5)
    due = reg.due_for_reap()
    ids = {d["uuid"] for d in due}
    assert "dead" in ids and "live" not in ids
    reg.set_status("dead", "expired")
    assert reg.active_count_for_ip("2.2.2.2") == 0
    assert reg.total_active() == 1


def test_killed_status_frees_ip(tmp_path):
    reg = Registry(str(tmp_path / "r.sqlite"))
    _mk(reg, "u1", "5.5.5.5")
    assert reg.active_count_for_ip("5.5.5.5") == 1
    reg.set_status("u1", "killed")
    assert reg.active_count_for_ip("5.5.5.5") == 0
