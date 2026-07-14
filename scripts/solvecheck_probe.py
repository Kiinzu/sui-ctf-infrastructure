"""Runs the orchestrator's real solve-check (challenge.py + sui_rpc.py) against a
live Sui RPC. Used by scripts/local_pipeline_test.sh. Reads config from env:

    RPC_URL, PACKAGE_ID,
    DRAINED_POOL_ID, DRAINED_POOL_TYPE   (expected: solved=True)
    FRESH_POOL_ID,   FRESH_POOL_TYPE     (expected: solved=False)

Exit 0 iff both expectations hold.
"""
import asyncio
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "orchestrator"))

from app.challenge import check_solved, load_challenge  # noqa: E402
from app.sui_rpc import SuiRpc  # noqa: E402


async def main() -> int:
    ch = load_challenge(os.path.join(REPO, "challenges"), "placeholder-flashpool")
    rpc = SuiRpc(os.environ["RPC_URL"])
    pkg = os.environ["PACKAGE_ID"]

    drained = {
        "objects": [{"objectId": os.environ["DRAINED_POOL_ID"],
                     "type": os.environ["DRAINED_POOL_TYPE"]}],
        "package_id": pkg,
    }
    fresh = {
        "objects": [{"objectId": os.environ["FRESH_POOL_ID"],
                     "type": os.environ["FRESH_POOL_TYPE"]}],
        "package_id": pkg,
    }

    d = await check_solved(ch, rpc, drained)
    f = await check_solved(ch, rpc, fresh)
    print(f"    solve-check drained pool -> {d} (expect True)")
    print(f"    solve-check fresh pool   -> {f} (expect False)")
    return 0 if (d is True and f is False) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
