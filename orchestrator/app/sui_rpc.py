"""Minimal async Sui JSON-RPC client.

Used by the orchestrator to (a) poll an instance for readiness and (b) run
solve-checks (object state reads / event queries). Talks directly to the
instance container over the internal Docker network — never through the public
`/uuid` proxy.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx


class SuiRpcError(Exception):
    pass


class SuiRpc:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self._timeout = timeout

    async def call(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self.url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise SuiRpcError(str(data["error"]))
        return data.get("result")

    async def is_ready(self) -> bool:
        """True once the fullnode answers a basic query."""
        try:
            await self.call("sui_getChainIdentifier", [])
            return True
        except Exception:
            return False

    async def get_object_content(self, object_id: str) -> Optional[dict[str, Any]]:
        """Return the Move object's `content` (type + fields) or None."""
        result = await self.call(
            "sui_getObject",
            [object_id, {"showContent": True, "showType": True}],
        )
        if not result or "data" not in result or result["data"] is None:
            return None
        return result["data"].get("content")

    async def query_event_exists(self, event_type: str,
                                 sender: Optional[str] = None) -> bool:
        """True if at least one event of `event_type` (optionally from `sender`)
        has been emitted on this instance."""
        event_filter: dict[str, Any] = {"MoveEventType": event_type}
        result = await self.call(
            "suix_queryEvents",
            [event_filter, None, 50, True],  # descending, newest first
        )
        events = (result or {}).get("data", []) if isinstance(result, dict) else []
        if sender is None:
            return len(events) > 0
        return any(ev.get("sender") == sender for ev in events)
