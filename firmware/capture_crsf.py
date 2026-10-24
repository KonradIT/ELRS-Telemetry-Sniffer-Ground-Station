#!/usr/bin/env python3
"""Capture and validate the sniffer's binary USB CRSF stream.

Install the live capture dependency with: python -m pip install pyserial
Examples:
  python firmware/capture_crsf.py COM27 --duration 30 --output capture.bin
  python firmware/capture_crsf.py --input capture.bin --quiet

The output file preserves every received byte, including startup text/noise.
Only frames with valid CRSF lengths and CRC8/DVB-S2 checksums are counted.
Exit status: 0 = aircraft telemetry seen, 1 = no aircraft telemetry, 2 = error.
The sniffer can synthesize LINK frames even without RF reception; those
alone do not establish that telemetry from the aircraft is being received.
"""

import argparse
from collections import Counter
from contextlib import ExitStack
import json
from pathlib import Path
import struct
import sys
import time


MAX_FRAME_SIZE = 64
ADDRESSES = frozenset((0x00, 0x10, 0x80, 0x8A, 0xC0, 0xC2, 0xC4,
                       0xC8, 0xCA, 0xCC, 0xEA, 0xEC, 0xEE, 0xEF))
TYPE_NAMES = {
    0x02: "GPS", 0x07: "VARIO", 0x08: "BATTERY", 0x09: "BARO",
    0x14: "LINK", 0x16: "RC_CHANNELS", 0x1E: "ATTITUDE", 0x21: "FLIGHT_MODE",
    0x28: "DEVICE_PING", 0x29: "DEVICE_INFO", 0x2B: "PARAMETER_ENTRY",
    0x2C: "PARAMETER_READ", 0x2D: "PARAMETER_WRITE", 0x32: "COMMAND",
    0x3A: "RADIO_ID", 0x7A: "MSP_REQ", 0x7B: "MSP_RESP", 0x7C: "MSP_WRITE",
}
# A known telemetry type plus a sufficient payload is required for success.
AIRCRAFT_PAYLOAD_MIN = {0x02: 15, 0x07: 2, 0x08: 8, 0x09: 2, 0x1E: 6, 0x21: 1}


def crc8_dvb_s2(data):
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ (0xD5 if crc & 0x80 else 0)) & 0xFF
    return crc


def type_name(frame_type):
    return TYPE_NAMES.get(frame_type, f"TYPE_0x{frame_type:02X}")


class FrameDecoder:
    """Recover CRSF frames across USB reads, bad CRCs and startup noise."""

    def __init__(self):
        self.buffer = bytearray()
        self.discarded_bytes = 0
        self.bad_crc_candidates = 0

    def feed(self, data, final=False):
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 2:
            size = self.buffer[1] + 2
            if self.buffer[0] not in ADDRESSES or not 4 <= size <= MAX_FRAME_SIZE:
                self._discard()
                continue
            if len(self.buffer) < size:
                if final:
                    self._discard()
                    continue
                break
            candidate = bytes(self.buffer[:size])
            if crc8_dvb_s2(candidate[2:-1]) != candidate[-1]:
                self.bad_crc_candidates += 1
                self._discard()
                continue
            frames.append(candidate)
            del self.buffer[:size]
        if final:
            self.discarded_bytes += len(self.buffer)
            self.buffer.clear()
        return frames

    def _discard(self):
        del self.buffer[0]
        self.discarded_bytes += 1


def describe_frame(frame):
    """Decode a few useful fields without depending on the GCS application."""
    frame_type, payload = frame[2], frame[3:-1]
    if frame_type == 0x02 and len(payload) >= 15:
        lat, lon, speed, heading, altitude, sats = struct.unpack_from(">iiHHHB", payload)
        return (f"lat={lat / 1e7:.7f} lon={lon / 1e7:.7f} alt={altitude - 1000}m "
                f"speed={speed / 10:.1f}km/h heading={heading / 100:.2f} sats={sats}")
    if frame_type == 0x08 and len(payload) >= 8:
        voltage, current = struct.unpack_from(">HH", payload)
        capacity = int.from_bytes(payload[4:7], "big")
        return f"voltage={voltage / 10:.1f}V current={current / 10:.1f}A capacity={capacity}mAh remaining={payload[7]}%"
    if frame_type == 0x1E and len(payload) >= 6:
        pitch, roll, yaw = struct.unpack_from(">hhh", payload)
        return f"pitch={pitch / 1e4:.4f}rad roll={roll / 1e4:.4f}rad yaw={yaw / 1e4:.4f}rad"
    if frame_type == 0x21:
        return "mode=" + payload.split(b"\0", 1)[0].decode("ascii", errors="replace")
    if frame_type == 0x14 and len(payload) >= 10:
        snr = struct.unpack_from("b", payload, 3)[0]
        return f"rssi=-{payload[0]}dBm lq={payload[2]}% snr={snr}dB (link statistics only)"
    return "payload=" + payload.hex(" ")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", nargs="?", help="USB serial port, e.g. COM27 or /dev/ttyACM0")
    parser.add_argument("--input", type=Path, help="validate a saved binary stream instead of opening a port")
    parser.add_argument("--duration", type=float, default=30.0, help="capture seconds; 0 means until Ctrl+C (default: 30)")
    parser.add_argument("--baud", type=int, default=460800, help="serial baud rate (default: 460800; USB CDC ignores the line rate)")
    parser.add_argument("--output", type=Path, help="save every received byte to this binary file")
    parser.add_argument("--summary", type=Path, help="also save the final summary as JSON")
    parser.add_argument("--quiet", action="store_true", help="print only the final summary")
    args = parser.parse_args()
    if bool(args.port) == bool(args.input):
        parser.error("provide a serial port OR --input")
    if args.duration < 0 or args.baud <= 0:
        parser.error("duration must be nonnegative and baud must be positive")
    paths = [path.resolve() for path in (args.input, args.output, args.summary) if path]
    if len(set(paths)) != len(paths):
        parser.error("input, output and summary must use different files")
    return args


def main():
    args = parse_args()
    decoder = FrameDecoder()
    counts = Counter()
    total_bytes = 0
    aircraft_frames = 0
    start = time.monotonic()
    error = None

    def consume(data, final=False):
        nonlocal aircraft_frames
        for frame in decoder.feed(data, final=final):
            frame_type = frame[2]
            counts[frame_type] += 1
            minimum = AIRCRAFT_PAYLOAD_MIN.get(frame_type)
            if minimum is not None and len(frame) - 4 >= minimum:
                aircraft_frames += 1
            if not args.quiet:
                elapsed = time.monotonic() - start
                print(f"{elapsed:8.3f}s {type_name(frame_type):12s} {frame.hex(' ')} | {describe_frame(frame)}", flush=True)

    try:
        with ExitStack() as stack:
            if args.input:
                source = stack.enter_context(args.input.open("rb"))
            else:
                try:
                    import serial
                except ImportError:
                    raise RuntimeError("pyserial is required: python -m pip install pyserial") from None
                # Preserve normal run mode. DTR tells the USB CDC device a host
                # is listening; RTS stays low so it does not request reset.
                source = serial.Serial(port=None, baudrate=args.baud, timeout=0.2)
                source.dtr = True
                source.rts = False
                source.port = args.port
                source.open()
                stack.callback(source.close)
            output = stack.enter_context(args.output.open("wb")) if args.output else None
            start = time.monotonic()
            while args.input or args.duration == 0 or time.monotonic() - start < args.duration:
                data = source.read(4096 if args.input else max(1, min(source.in_waiting, 4096)))
                if args.input and not data:
                    break
                total_bytes += len(data)
                if output:
                    output.write(data)
                consume(data)
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as exc:
        error = str(exc)
        print(f"Capture error: {error}", file=sys.stderr)
    consume(b"", final=True)

    summary = {
        "source": str(args.input or args.port),
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "received_bytes": total_bytes,
        "valid_frames": sum(counts.values()),
        "aircraft_telemetry_frames": aircraft_frames,
        "link_statistics_frames": counts[0x14],
        "discarded_bytes": decoder.discarded_bytes,
        "bad_crc_candidates": decoder.bad_crc_candidates,
        "frame_types": {type_name(key): value for key, value in sorted(counts.items())},
    }
    if error:
        summary["error"] = error
    print(json.dumps(summary, indent=2))
    if args.summary:
        try:
            args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Cannot save summary: {exc}", file=sys.stderr)
            return 2
    if not aircraft_frames:
        print("No aircraft telemetry verified; LINK frames alone are not evidence of aircraft reception.", file=sys.stderr)
    return 2 if error else (0 if aircraft_frames else 1)


if __name__ == "__main__":
    sys.exit(main())
