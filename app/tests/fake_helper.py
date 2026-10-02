"""Stands in for portal_helper.py: speaks the same protocol with made-up frames."""

import json
import struct
import sys
import time

out = sys.stdout.buffer


def send(header, payload=None):
    if payload is not None:
        header["size"] = len(payload)
    raw = json.dumps(header).encode()
    out.write(struct.pack("<I", len(raw)) + raw + (payload or b""))
    out.flush()


mode = sys.argv[1] if len(sys.argv) > 1 else "ok"

if mode == "denied":
    send({"type": "status", "state": "awaiting_consent"})
    send({"type": "error", "message": "Start: screen sharing was declined"})
    sys.exit(2)
if mode == "silent":
    time.sleep(30)
    sys.exit(0)

send({"type": "status", "state": "awaiting_consent"})
send({"type": "streams", "streams": [
    {"index": 0, "left": 0, "top": 0, "width": 8, "height": 4, "width_mm": 100},
    {"index": 1, "left": 8, "top": 0, "width": 5, "height": 2, "width_mm": None},
]})
# Monitor 0: tight rows. Monitor 1: 5 px wide, rows padded to 16 bytes.
send({"type": "frame", "index": 0, "width": 8, "height": 4, "stride": 24}, bytes([7]) * 96)
row = bytes([1, 2, 3] * 5) + b"\xff"
send({"type": "frame", "index": 1, "width": 5, "height": 2, "stride": 16}, row * 2)
if mode == "crash":
    sys.exit(1)
send({"type": "frame", "index": 0, "width": 8, "height": 4, "stride": 24}, bytes([9]) * 96)

sys.stdin.buffer.read()  # exit when the parent closes stdin, like the real helper
