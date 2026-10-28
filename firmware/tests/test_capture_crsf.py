"""Binary stream regression checks; run: python -m unittest discover -s firmware/tests"""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "capture_crsf.py"
SPEC = importlib.util.spec_from_file_location("capture_crsf", SCRIPT)
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def frame(frame_type, payload, address=0xEA):
    body = bytes((frame_type,)) + payload
    return bytes((address, len(body) + 1)) + body + bytes((capture.crc8_dvb_s2(body),))


BATTERY = frame(0x08, bytes.fromhex("00a8001500012c55"))
LINK = frame(0x14, bytes(10))


class StreamTests(unittest.TestCase):
    def test_standard_crc_check_value(self):
        # CRC-8/DVB-S2's standard check value is 0xBC.
        self.assertEqual(capture.crc8_dvb_s2(b"123456789"), 0xBC)

    def test_all_fragment_boundaries_and_concatenation(self):
        data = BATTERY + LINK + BATTERY
        for split in range(len(data) + 1):
            decoder = capture.FrameDecoder()
            decoded = decoder.feed(data[:split]) + decoder.feed(data[split:])
            self.assertEqual(decoded, [BATTERY, LINK, BATTERY], f"split={split}")
            self.assertEqual(decoder.discarded_bytes, 0)

    def test_noise_bad_crc_and_truncated_final_header(self):
        decoder = capture.FrameDecoder()
        damaged = bytearray(BATTERY)
        damaged[-1] ^= 0x01
        data = b"ESP-ROM startup\r\n" + damaged + LINK + b"\xea\x3e" + BATTERY
        decoded = decoder.feed(data) + decoder.feed(b"", final=True)
        self.assertEqual(decoded, [LINK, BATTERY])
        self.assertGreaterEqual(decoder.bad_crc_candidates, 1)
        self.assertEqual(decoder.discarded_bytes, len(data) - len(LINK) - len(BATTERY))

    def test_maximum_size_and_invalid_lengths(self):
        maximum = frame(0x7B, bytes(60))
        self.assertEqual(len(maximum), 64)
        decoder = capture.FrameDecoder()
        self.assertEqual(decoder.feed(b"\xea\x00\xea\x3f" + maximum), [maximum])
        self.assertEqual(decoder.discarded_bytes, 4)

    def test_binary_file_validation_and_aircraft_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "capture.bin"
            output = Path(directory) / "copy.bin"
            for stream, expected_status, expected_aircraft in (
                    (LINK * 3, 1, 0),
                    (b"startup\n" + LINK + BATTERY, 0, 1),
                    (frame(0x02, b""), 1, 0)):
                source.write_bytes(stream)
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "--input", str(source),
                     "--output", str(output), "--quiet"], capture_output=True, text=True)
                self.assertEqual(result.returncode, expected_status, result.stderr)
                self.assertEqual(json.loads(result.stdout)["aircraft_telemetry_frames"], expected_aircraft)
                self.assertEqual(output.read_bytes(), stream)


if __name__ == "__main__":
    unittest.main()
