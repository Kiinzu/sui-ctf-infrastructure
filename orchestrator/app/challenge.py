"""Per-challenge manifest loading + solve-check dispatch.

A challenge is a directory under CHALLENGES_DIR containing `challenge.yml` and a
Move `package/`. This module turns that manifest into the values the instance
container needs at deploy time, and evaluates the win condition for `/flag`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from .sui_rpc import SuiRpc

_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
}


@dataclass(frozen=True)
class Challenge:
    name: str
    dir: str
    flag: str
    sui_version: str
    ttl_seconds: Optional[int]
    package_path: str
    player_faucet_coins: int
    deploy: list[dict[str, Any]]
    solve: dict[str, Any]
    flag_mode: str
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def package_abs_path(self) -> str:
        return os.path.join(self.dir, self.package_path)


def load_challenge(challenges_dir: str, name: str) -> Challenge:
    cdir = os.path.join(challenges_dir, name)
    manifest = os.path.join(cdir, "challenge.yml")
    if not os.path.isfile(manifest):
        raise FileNotFoundError(f"challenge manifest not found: {manifest}")
    with open(manifest, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    required = ["name", "flag", "package_path", "solve"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"{manifest}: missing required keys: {missing}")

    return Challenge(
        name=data["name"],
        dir=cdir,
        flag=data["flag"],
        sui_version=data.get("sui_version", "mainnet-v1.74.1"),
        ttl_seconds=data.get("ttl_seconds"),
        package_path=data["package_path"],
        player_faucet_coins=int(data.get("player_faucet_coins", 2)),
        deploy=list(data.get("deploy", [])),
        solve=dict(data["solve"]),
        flag_mode=data.get("flag_mode", "server_issued"),
        description=data.get("description", ""),
        raw=data,
    )


def _coerce_pair(a: Any, b: Any) -> tuple[Any, Any]:
    """Move u64/u128 fields come back as strings; compare numerically when both
    sides look numeric, otherwise fall back to string comparison."""
    try:
        return int(a), int(b)
    except (TypeError, ValueError):
        return str(a), str(b)


async def check_solved(challenge: Challenge, rpc: SuiRpc,
                       instance: dict[str, Any]) -> bool:
    """Evaluate the challenge win condition against a live instance."""
    spec = challenge.solve
    kind = spec.get("type", "state")

    if kind == "state":
        return await _check_state(spec, rpc, instance)
    if kind == "event":
        return await _check_event(spec, rpc, instance)
    raise ValueError(f"unknown solve.type: {kind!r}")


async def _check_state(spec: dict[str, Any], rpc: SuiRpc,
                       instance: dict[str, Any]) -> bool:
    type_suffix = spec["object_type"]
    target = _find_object(instance.get("objects", []), type_suffix)
    if target is None:
        return False
    content = await rpc.get_object_content(target["objectId"])
    if not content or "fields" not in content:
        return False
    field_name = spec["field"]
    if field_name not in content["fields"]:
        return False
    actual, expected = _coerce_pair(content["fields"][field_name], spec["value"])
    op = _OPS.get(spec.get("op", "eq"))
    if op is None:
        raise ValueError(f"unknown solve.op: {spec.get('op')!r}")
    return bool(op(actual, expected))


async def _check_event(spec: dict[str, Any], rpc: SuiRpc,
                       instance: dict[str, Any]) -> bool:
    # Event type suffix (e.g. "::pool::FlagClaimed") -> full type with pkg id.
    package_id = instance.get("package_id")
    if not package_id:
        return False
    suffix = spec["event_type"]
    full_type = f"{package_id}{suffix}" if suffix.startswith("::") else suffix
    # Bind the emitter to the PLAYER by default, so a win event emitted by another
    # party (or pre-existing) doesn't satisfy the check. Authors can override with
    # `sender: any` (no binding) or `sender: <address>` (pin a specific one).
    sender_spec = spec.get("sender", "player")
    if sender_spec == "player":
        sender = instance.get("player_address")
    elif sender_spec in (None, "any"):
        sender = None
    else:
        sender = sender_spec
    return await rpc.query_event_exists(full_type, sender=sender)


def _find_object(objects: list[dict[str, Any]], type_suffix: str) -> Optional[dict[str, Any]]:
    for obj in objects:
        otype = obj.get("type") or ""
        # Strip any generic type-argument tail so a generic struct still matches:
        #   "0xpkg::pool::Pool<0x2::sui::SUI>" -> "0xpkg::pool::Pool"
        base = otype.split("<", 1)[0]
        if base.endswith(type_suffix):
            return obj
    return None
