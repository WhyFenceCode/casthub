#!/usr/bin/env python3
"""Pretend to be Windows casting to a running hub, over loopback: MS-MICE SOURCE_READY, the WFD
RTSP source side (M1-M7, M16), then an H.264 + AAC MPEG-TS/RTP test stream for --seconds.

    python3 tools/probe_receiver.py [--port 7250] [--seconds 8]

Prints what happened; the hub's own log says the rest. Writes nothing.
"""

import argparse
import asyncio
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from casthub.mice import protocol as mp  # noqa: E402
from casthub.rtsp import message as rm  # noqa: E402


async def source(reader, writer, seconds, done):
    cseq = 0
    rtp_port = None

    def send(msg):
        writer.write(msg.encode())

    async def serve_until_response():
        nonlocal rtp_port
        while True:
            msg = await rm.read_message(reader)
            if not msg.is_request:
                return msg
            if msg.method == "SETUP":
                rtp_port = int(msg.headers["transport"].split("client_port=")[1].split(";")[0].split("-")[0])
                send(rm.response(msg, session="00000001;timeout=30", transport=msg.headers["transport"]))
            else:
                send(rm.response(msg, public="org.wfa.wfd1.0, SETUP, TEARDOWN, PLAY, PAUSE, GET_PARAMETER, SET_PARAMETER"))
                if msg.method == "PLAY":
                    return None

    async def req(method, body="", **h):
        nonlocal cseq
        cseq += 1
        send(rm.request(method, "rtsp://localhost/wfd1.0" if method != "OPTIONS" else "*", cseq, body, **h))
        return await serve_until_response()

    await req("OPTIONS", require="org.wfa.wfd1.0")
    r = await req("GET_PARAMETER", "wfd_video_formats\r\nwfd_audio_codecs\r\nwfd_client_rtp_ports\r\n")
    print("M3 reply:\n  " + r.body.strip().replace("\r\n", "\n  "))
    await req("SET_PARAMETER",
              "wfd_video_formats: 00 00 02 10 00000020 00000000 00000000 00 0000 0000 00 none none\r\n"
              "wfd_audio_codecs: AAC 00000001 00\r\n"
              "wfd_presentation_URL: rtsp://127.0.0.1/wfd1.0/streamid=0 none\r\n"
              "wfd_client_rtp_ports: RTP/AVP/UDP;unicast 1028 0 mode=play\r\n")
    cseq += 1
    send(rm.request("SET_PARAMETER", "rtsp://localhost/wfd1.0", cseq, "wfd_trigger_method: SETUP\r\n"))
    while await serve_until_response() is not None or rtp_port is None:
        pass
    print(f"PLAY received; streaming 1280x720 to UDP {rtp_port} for {seconds} s")
    gst = subprocess.Popen(
        ["gst-launch-1.0", "-q", "videotestsrc", "is-live=true", "pattern=smpte", "!",
         "video/x-raw,width=1280,height=720,framerate=30/1", "!", "vah264enc", "!", "h264parse", "!",
         "mpegtsmux", "name=mux", "!", "rtpmp2tpay", "!", "udpsink", "host=127.0.0.1", f"port={rtp_port}",
         "audiotestsrc", "is-live=true", "volume=0.05", "!", "avenc_aac", "!", "aacparse", "!", "mux."])
    try:
        await asyncio.sleep(seconds)
    finally:
        gst.terminate()
    done.set()


async def main(a):
    done = asyncio.Event()
    server = await asyncio.start_server(lambda r, w: source(r, w, a.seconds, done), "127.0.0.1", 0)
    rtsp_port = server.sockets[0].getsockname()[1]
    r, w = await asyncio.open_connection("127.0.0.1", a.port)
    w.write(mp.source_ready("Cast Hub probe", rtsp_port, uuid.UUID(int=7)))
    print(f"SOURCE_READY sent (RTSP on {rtsp_port})")
    try:
        await asyncio.wait_for(done.wait(), a.seconds + 20)
    except TimeoutError:
        print("FAILED: the hub never finished the handshake (see its log)")
        return 1
    w.write(mp.stop_projection("Cast Hub probe", uuid.UUID(int=7)))
    await w.drain()
    w.close()
    print("STOP_PROJECTION sent")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7250)
    ap.add_argument("--seconds", type=float, default=8)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
