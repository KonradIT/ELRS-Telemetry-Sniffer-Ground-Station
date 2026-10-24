"""Check every upstream patch anchor against a pristine ExpressLRS 3.5.6 tree.

Set ELRS_SRC to an ExpressLRS 3.5.6 src checkout to run the integration tests.
Pristine file contents are taken from git (HEAD, the 3.5.6 tag) so an already
integrated working tree cannot mask anchor problems. Nothing is modified.
"""

from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


FIRMWARE = Path(__file__).resolve().parents[1]
DEFAULT_ELRS = FIRMWARE.parents[1] / "ExpressLRS-t3s3-lr1121" / "src"
ELRS = Path(os.environ.get("ELRS_SRC", DEFAULT_ELRS))


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, FIRMWARE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


integration = load_module("integrate")


def pristine(relative):
    """The ExpressLRS 3.5.6 version of ``relative`` (a path below src/)."""
    result = subprocess.run(
        ["git", "-C", str(ELRS.parent), "show", f"HEAD:{ELRS.name}/{relative}"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise unittest.SkipTest(f"git could not provide a pristine {relative}: {result.stderr.strip()}")
    return result.stdout


def apply_to_text(text, edits, upgrades=()):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "source.txt"
        path.write_text(text, encoding="utf-8", newline="\n")
        with redirect_stdout(io.StringIO()):
            return integration.apply_edits(str(path), edits, upgrades)


@unittest.skipUnless((ELRS / "src/rx_main.cpp").exists(), "Set ELRS_SRC to an ELRS 3.5.6 src checkout")
class IntegrationAnchorTests(unittest.TestCase):
    def test_every_anchor_matches_pristine_sources_exactly_once(self):
        for relative, edits in integration.combined_edits().items():
            with self.subTest(file=relative):
                text = pristine(relative)
                for anchor, replacement in edits:
                    self.assertEqual(text.count(anchor), 1, f"{relative}: {anchor[:80]!r}")
                    self.assertNotIn(replacement, text, f"{relative}: replacement already present before applying")
                    text = text.replace(anchor, replacement)
                # A second pass must detect every edit as already applied.
                self.assertEqual(apply_to_text(text, edits, integration.UPGRADES.get(relative, ())), text)

    def test_legacy_integrations_upgrade_to_the_current_edits(self):
        for relative, pairs in integration.UPGRADES.items():
            with self.subTest(file=relative):
                edits = integration.combined_edits()[relative]
                fresh = apply_to_text(pristine(relative), edits)
                legacy = fresh
                for old, new in pairs:
                    self.assertIn(new, fresh, f"{relative}: {new[:80]!r}")
                    self.assertNotEqual(old, new)
                    legacy = legacy.replace(new, old)
                self.assertNotEqual(legacy, fresh)
                self.assertEqual(apply_to_text(legacy, edits, pairs), fresh)

    def test_sx126x_radio_type_reaches_every_radio_switch(self):
        combined = integration.combined_edits()
        for relative in ("include/common.h", "include/targets.h", "src/common.cpp", "lib/FHSS/FHSS.h",
                         "lib/FHSS/FHSS.cpp", "lib/DEVICE/device.cpp", "python/build_html.py",
                         "python/build_flags.py"):
            with self.subTest(file=relative):
                text = pristine(relative)
                for anchor, replacement in combined[relative]:
                    text = text.replace(anchor, replacement)
                self.assertIn("RADIO_SX126X", text)
        common = pristine("src/common.cpp")
        for anchor, replacement in combined["src/common.cpp"]:
            common = common.replace(anchor, replacement)
        table = common[common.index("#if defined(RADIO_SX126X)"):common.index("#if defined(RADIO_SX128X)")]
        rows = [line for line in table.splitlines() if line.strip().startswith("{") and "RATE_" in line]
        self.assertEqual(len(rows), 14)
        self.assertEqual([int(row.split(",")[0].strip(" {")) for row in rows], list(range(14)))
        supported = [row for row in rows if "RADIO_TYPE_SX126x_LORA" in row]
        self.assertEqual(len(supported), 5)
        for row in supported:
            self.assertNotIn("SX126X_LORA_SF6", row, "SX127x-style SF6 is not demodulable by an SX126x")


class PatchSetConsistencyTests(unittest.TestCase):
    def test_patch_sets_touch_disjoint_anchors(self):
        combined = integration.combined_edits()
        for relative, edits in combined.items():
            anchors = [anchor for anchor, _ in edits]
            self.assertEqual(len(anchors), len(set(anchors)), relative)

    def test_library_and_board_files_exist(self):
        for library, files in integration.LIB_FILES.items():
            for name in files:
                self.assertTrue((FIRMWARE / "lib" / library / name).is_file(), name)
        for name in integration.BOARD_FILES:
            self.assertTrue((FIRMWARE / "boards" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
