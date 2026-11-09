"""Passthrough (M10, SPEC D29): every cast goes on to a Miracast TV, Cast Hub as its source.

    hub ── tv_on ──▶ casthub-p2p: find the TV, join it as group owner (radio slot: hotspot paused)
    TV ── RTSP ──▶ our Miracast source on 7236 (rtsp/source.py): the TV picks a mode
    hub: TvProcess (media/tvproc.py) sends to the TV; the receivers play casts into it

States (shown on the leave page): off · searching · connecting · on · failed. While wanted, a
lost TV is joined again after RETRY_S. `fixed` mode (casthub.toml) turns it on at start with the
configured target and the on-screen choice is locked.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from casthub.media.tv import TvTarget
from casthub.media.tvproc import TvProcess
from casthub.rtsp import source as rtsp_source
from casthub.rtsp.sink import RtspError

if TYPE_CHECKING:
    from casthub.hub.__main__ import Hub

log = logging.getLogger("casthub.hub")
RETRY_S = 5
FIND_TIMEOUT_S = 25


class Passthrough:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.want = ""                     # the TV we're sending to ("" = off)
        self.state = "off"
        self.why = ""                      # failed: the reason
        self.tvs: list[str] = []
        self._found: asyncio.Future | None = None
        self.task: asyncio.Task | None = None
        self.source: rtsp_source.RtspSource | None = None
        self.proc: TvProcess | None = None
        self.factory = TvProcess           # tests swap these
        self.source_factory = rtsp_source.RtspSource

    @property
    def locked(self) -> bool:
        return self.hub.cfg.passthrough_mode == "fixed"

    @property
    def allowed(self) -> bool:
        return self.hub.cfg.passthrough_mode != "off"

    def status(self) -> dict:
        return {"cmd": "passthrough", "state": self.state, "tv": self.want, "why": self.why,
                "tvs": self.tvs, "locked": self.locked, "allowed": self.allowed}

    async def _set(self, state: str, why: str = "") -> None:
        if (state, why) != (self.state, self.why):
            log.info("passthrough: %s%s", state, f" ({why})" if why else "")
        self.state, self.why = state, why
        await self.hub.ui.send(self.status())

    # ---- what the leave page (or fixed mode) asks ---------------------------------------------
    async def find(self) -> list[str]:
        """Search for Miracast TVs (casthub-p2p, ~8 s)."""
        self._found = asyncio.get_running_loop().create_future()
        if not await self.hub.p2p_send({"cmd": "tv_find"}):
            return []
        with contextlib.suppress(TimeoutError):
            self.tvs = list(await asyncio.wait_for(self._found, FIND_TIMEOUT_S))
        await self.hub.ui.send(self.status())
        return self.tvs

    async def start(self, name: str) -> None:
        if not self.allowed or not name:
            return
        if self.want == name and self.state in ("searching", "connecting", "on"):
            return
        await self.stop(tell_p2p=False)
        self.want = name
        await self.hub.end_all()           # casts on our screens end; the next one goes to the TV
        self.task = asyncio.create_task(self._run())

    async def stop(self, tell_p2p: bool = True) -> None:
        wanted, self.want = self.want, ""
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
            self.task = None
        await self._drop(teardown=True)
        if wanted:
            await self.hub.end_all()
        if tell_p2p and wanted:
            await self.hub.p2p_send({"cmd": "tv_off"})
        await self._set("off")

    # ---- events from casthub-p2p ------------------------------------------------------------------
    def on_p2p_event(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "tvs" and self._found and not self._found.done():
            self._found.set_result(ev.get("list", []))
        elif kind == "tv" and self.want:
            asyncio.ensure_future(self._set("connecting"))
        elif kind == "tv_failed" and self.want:
            asyncio.ensure_future(self._set("failed", str(ev.get("why", ""))))
            if self.source:
                self.source.close()           # _run retries
        elif kind == "tv_gone" and self.source:
            self.source.close()

    # ---- one TV session after another, while wanted ------------------------------------------------
    async def _run(self) -> None:
        while self.want:
            name = self.want
            try:
                await self._session(name)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 (logged; retried while wanted)
                log.warning("passthrough: %s", e)
                if self.state != "failed":
                    await self._set("failed", str(e))
            await self._drop(teardown=False)
            if self.want:
                await asyncio.sleep(RETRY_S)

    async def _session(self, name: str) -> None:
        await self._set("searching")
        self.source = self.source_factory("0.0.0.0", rtsp_source.DEFAULT_PORT)
        await self.source.listen()
        if not await self.hub.p2p_send({"cmd": "tv_on", "name": name}):
            raise RuntimeError("casthub-p2p is not running")
        session = await self.source.accept()
        target = TvTarget(session.sink_host, session.rtp_port, session.video.width, session.video.height,
                          session.video.fps, session.profile, session.audio.codec if session.audio else "")
        lost = asyncio.get_running_loop().create_future()
        self.proc = self.factory(target, lambda text: lost.done() or lost.set_result(text))
        await self.proc.start()
        self.source.on_idr = self.proc.keyframe
        shown = session.name or name
        for r in self.hub.receivers:
            await r.set_tv(self.proc, shown)
        await self._set("on")
        await self.hub.p2p_send({"cmd": "tv_ready"})     # dual-role again: Win + K lists us
        serving = asyncio.ensure_future(self.source.serve())
        done, _ = await asyncio.wait({serving, lost}, return_when=asyncio.FIRST_COMPLETED)
        why = next(iter(done)).result()
        serving.cancel()
        if self.want:
            await self._set("failed", f"the TV session ended ({why})")

    async def _drop(self, teardown: bool) -> None:
        """Back to our screens: receivers off the TV, the sender stopped, RTSP closed."""
        for r in self.hub.receivers:
            if r.tv is not None:
                await r.set_tv(None)
        if self.proc:
            self.proc.stop()
            self.proc = None
        if self.source:
            src, self.source = self.source, None
            if teardown:
                with contextlib.suppress(RtspError, TimeoutError, Exception):
                    await asyncio.wait_for(src.teardown(), 3)
            src.close()
