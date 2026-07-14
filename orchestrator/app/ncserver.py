"""Raw-TCP `nc` menu — the sole player interface.

    $ nc <host> <port>
    1 - spawn instance
    2 - kill instance
    3 - flag
    >>

Spawning is gated by a proof-of-work puzzle and a "1 live instance per source IP"
cap. All actions key off the instance UUID, which doubles as the bearer credential.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from .config import Config
from .instances import InstanceManager, SpawnError
from .pow import new_challenge

log = logging.getLogger("suictf.nc")

# Connection-flood backstops (unauthenticated pre-PoW connections).
_MAX_CONN = int(os.environ.get("NC_MAX_CONN", "").strip() or 200)
_MAX_CONN_PER_IP = int(os.environ.get("NC_MAX_CONN_PER_IP", "").strip() or 4)

MENU = (
    "\n"
    "1 - spawn instance\n"
    "2 - kill instance\n"
    "3 - flag\n"
    ">> "
)


class NcServer:
    def __init__(self, *, config: Config, manager: InstanceManager):
        self.cfg = config
        self.mgr = manager
        self._server: Optional[asyncio.AbstractServer] = None
        self._active = 0
        self._per_ip: dict[str, int] = {}

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, host="0.0.0.0", port=self.cfg.nc_port
        )
        log.info("nc menu listening on 0.0.0.0:%s", self.cfg.nc_port)

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    # --------------------------------------------------------------- helpers
    @staticmethod
    async def _send(writer: asyncio.StreamWriter, text: str) -> None:
        writer.write(text.encode())
        await writer.drain()

    async def _readline(self, reader: asyncio.StreamReader,
                        timeout: Optional[int] = None) -> Optional[str]:
        timeout = timeout if timeout is not None else self.cfg.nc_read_timeout
        try:
            data = await asyncio.wait_for(reader.readline(), timeout)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None
        if not data:  # EOF / client disconnected
            return None
        return data.decode(errors="replace")

    # --------------------------------------------------------------- handler
    async def _handle(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else "unknown"
        if self._active >= _MAX_CONN or self._per_ip.get(ip, 0) >= _MAX_CONN_PER_IP:
            try:
                writer.write(b"too many connections, try again shortly.\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            log.info("nc connection from %s rejected (cap)", ip)
            return
        self._active += 1
        self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
        log.info("nc connection from %s (active=%d)", ip, self._active)
        try:
            await self._send(writer, self._banner())
            while True:
                await self._send(writer, MENU)
                choice = await self._readline(reader)
                if choice is None:
                    break
                choice = choice.strip()
                if choice == "1":
                    await self._do_spawn(reader, writer, ip)
                elif choice == "2":
                    await self._do_kill(reader, writer)
                elif choice == "3":
                    await self._do_flag(reader, writer)
                elif choice in ("q", "quit", "exit", "0"):
                    await self._send(writer, "bye!\n")
                    break
                elif choice == "":
                    continue
                else:
                    await self._send(writer, "invalid choice.\n")
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception:  # noqa: BLE001 - never crash the server on one client
            log.exception("nc handler error for %s", ip)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._active -= 1
            self._per_ip[ip] = self._per_ip.get(ip, 1) - 1
            if self._per_ip[ip] <= 0:
                self._per_ip.pop(ip, None)
            log.info("nc connection closed for %s", ip)

    def _banner(self) -> str:
        ch = self.mgr.ch
        return (
            "\n============================================================\n"
            f" {ch.name}\n"
            f" {ch.description}\n"
            "============================================================\n"
        )

    # ----------------------------------------------------------------- spawn
    async def _do_spawn(self, reader: asyncio.StreamReader,
                       writer: asyncio.StreamWriter, ip: str) -> None:
        if self.cfg.pow_difficulty_bits > 0:
            pc = new_challenge(self.cfg.pow_difficulty_bits)
            await self._send(
                writer,
                "\n== proof of work ==\n"
                "Solve this and paste the resulting number to spawn an instance.\n"
                "Run:\n\n"
                f"  {pc.solver_command()}\n\n"
                f"(finds a nonce with {pc.difficulty} leading zero bits; "
                "~a few seconds)\n"
                "solution: ",
            )
            sol = await self._readline(reader, self.cfg.pow_timeout)
            if sol is None or not pc.verify(sol.strip()):
                await self._send(
                    writer, "\n[!] proof-of-work failed or timed out.\n"
                )
                return
            await self._send(writer, "[+] proof-of-work accepted.\n")

        await self._send(
            writer,
            "\n[*] Spawning your isolated Sui network + deploying the challenge.\n"
            "    This can take up to ~2 minutes. Please wait",
        )
        heartbeat = asyncio.create_task(self._heartbeat(writer))
        try:
            result = await self.mgr.spawn(ip)
        except SpawnError as exc:
            heartbeat.cancel()
            await self._send(writer, f"\n\n[!] {exc}\n")
            return
        except Exception as exc:  # noqa: BLE001
            heartbeat.cancel()
            log.exception("spawn crashed")
            await self._send(writer, f"\n\n[!] Internal error: {exc}\n")
            return
        heartbeat.cancel()
        await self._send(writer, "\n" + self._format_instance(result))

    async def _heartbeat(self, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                writer.write(b" .")
                await writer.drain()
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            return

    def _format_instance(self, r: dict[str, Any]) -> str:
        lines = [
            "============================================================",
            f" Instance ready!   (challenge: {r['challenge']})",
            "============================================================",
            f" uuid          : {r['uuid']}",
            f" rpc_url       : {r['rpc_url']}",
            f" private_key   : {r['private_key']}",
            f" your address  : {r['player_address']}",
            f" package_id    : {r['package_id']}",
        ]
        if r.get("objects"):
            lines.append(" objects:")
            for obj in r["objects"]:
                lines.append(f"   - {obj.get('type')}  ->  {obj.get('objectId')}")
        lines += [
            f" expires in    : {_fmt_secs(r['seconds_remaining'])}  (auto-destroyed)",
            "------------------------------------------------------------",
            " Keep your uuid PRIVATE — anyone who has it (or your rpc_url, which",
            " contains it) can read/drain/claim/destroy your instance.",
            " Option 3 + your uuid = the flag; option 2 = destroy early.",
            "============================================================",
        ]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ kill
    async def _do_kill(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        await self._send(writer, "enter your instance uuid: ")
        uuid = await self._readline(reader)
        if uuid is None:
            return
        ok = await self.mgr.kill(uuid.strip())
        await self._send(
            writer,
            "[+] instance destroyed.\n" if ok else "[!] no such instance.\n",
        )

    # ------------------------------------------------------------------ flag
    async def _do_flag(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        await self._send(writer, "enter your instance uuid: ")
        uuid = await self._readline(reader)
        if uuid is None:
            return
        solved, msg = await self.mgr.get_flag(uuid.strip())
        if solved:
            await self._send(writer, f"\n[+] Solved! flag: {msg}\n")
        else:
            await self._send(writer, f"\n[!] {msg}\n")


def _fmt_secs(secs: int) -> str:
    m, s = divmod(max(0, secs), 60)
    return f"{m:02d}:{s:02d}"
