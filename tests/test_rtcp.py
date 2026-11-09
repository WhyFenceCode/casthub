"""M10: the TV's RTCP receiver reports (RFC 3550 6.4.2), logged so a black TV can be told apart
from a lossy link."""

import struct
import unittest

from casthub.rtsp.source import parse_rtcp


def rr(fraction: int, lost: int, seq: int, jitter: int) -> bytes:
    block = struct.pack("!IB3sIIII", 0x11, fraction, lost.to_bytes(3, "big"), seq, jitter, 0, 0)
    return struct.pack("!BBHI", 0x81, 201, 7, 0x22) + block


class TestRtcp(unittest.TestCase):
    def test_receiver_report(self):
        r = parse_rtcp(rr(64, 12, 70000, 900))
        self.assertEqual((r.fraction_lost, r.lost, r.highest_seq, r.jitter), (0.25, 12, 70000, 900))

    def test_compound_with_sdes_first(self):
        sdes = struct.pack("!BBHI", 0x81, 202, 2, 0x22) + b"\x00" * 4
        self.assertEqual(parse_rtcp(sdes + rr(0, 0, 5, 1)).highest_seq, 5)

    def test_not_a_report(self):
        self.assertIsNone(parse_rtcp(b""))
        self.assertIsNone(parse_rtcp(b"\x00\x01\x02\x03"))                  # not RTP version 2
        self.assertFalse(parse_rtcp(struct.pack("!BBHI", 0x80, 201, 1, 0x22)).received)  # no blocks: nothing
        self.assertTrue(parse_rtcp(rr(0, 0, 5, 1)).received)


if __name__ == "__main__":
    unittest.main()
