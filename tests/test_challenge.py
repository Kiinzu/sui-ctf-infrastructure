import asyncio
import dataclasses

from conftest import CHALLENGES_DIR

from app.challenge import check_solved, load_challenge


class FakeRpc:
    def __init__(self, balance=0, has_event=False):
        self.balance = balance
        self.has_event = has_event

    async def get_object_content(self, object_id):
        return {
            "dataType": "moveObject",
            "type": "0xpkg::pool::Pool",
            "fields": {"balance": str(self.balance), "id": {"id": object_id}},
        }

    async def query_event_exists(self, event_type, sender=None):
        return self.has_event


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_manifest_loads():
    ch = load_challenge(CHALLENGES_DIR, "placeholder-flashpool")
    assert ch.name == "placeholder-flashpool"
    assert ch.flag.startswith("flag{")
    assert ch.solve["type"] == "state"
    assert ch.solve["field"] == "balance"
    assert ch.flag_mode == "server_issued"
    assert ch.deploy[0]["function"] == "init_pool"


def test_state_check_solved_and_unsolved():
    ch = load_challenge(CHALLENGES_DIR, "placeholder-flashpool")
    inst = {
        "objects": [{"objectId": "0xpool", "type": "0xpkg::pool::Pool"}],
        "package_id": "0xpkg",
    }
    assert _run(check_solved(ch, FakeRpc(balance=0), inst)) is True
    assert _run(check_solved(ch, FakeRpc(balance=5), inst)) is False


def test_state_check_matches_generic_type():
    # A generic struct type (e.g. Pool<0x2::sui::SUI>) must still match the
    # "::pool::Pool" suffix after stripping the type-argument tail.
    ch = load_challenge(CHALLENGES_DIR, "placeholder-flashpool")
    inst = {
        "objects": [{"objectId": "0xpool", "type": "0xpkg::pool::Pool<0x2::sui::SUI>"}],
        "package_id": "0xpkg",
    }
    assert _run(check_solved(ch, FakeRpc(balance=0), inst)) is True


def test_state_check_missing_object_is_unsolved():
    ch = load_challenge(CHALLENGES_DIR, "placeholder-flashpool")
    inst = {"objects": [{"objectId": "0xx", "type": "0xpkg::other::Thing"}],
            "package_id": "0xpkg"}
    assert _run(check_solved(ch, FakeRpc(balance=0), inst)) is False


def test_event_check():
    ch = load_challenge(CHALLENGES_DIR, "placeholder-flashpool")
    event_ch = dataclasses.replace(
        ch, solve={"type": "event", "event_type": "::pool::FlagClaimed"}
    )
    inst = {"objects": [], "package_id": "0xpkg", "player_address": "0xplayer"}
    assert _run(check_solved(event_ch, FakeRpc(has_event=True), inst)) is True
    assert _run(check_solved(event_ch, FakeRpc(has_event=False), inst)) is False
