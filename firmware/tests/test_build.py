"""Protect firmware artifacts and keep filesystem uploads independent."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


FIRMWARE = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, FIRMWARE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_module("build")
integration = load_module("integrate")


def fake_checkout(directory):
    checkout = Path(directory)
    src = checkout / "src"
    (src / "src").mkdir(parents=True)
    (src / "targets").mkdir()
    (src / "include").mkdir()
    (src / "src/rx_main.cpp").touch()
    (src / "platformio.ini").write_text("extra_configs =\n\ttargets/common.ini\n")
    return checkout, src


def staged(checkout, environment, phrase="test-configuration", **kwargs):
    with patch.object(build.subprocess, "check_output", return_value="3.5.6"), \
            patch.object(build.subprocess, "run"), redirect_stdout(io.StringIO()):
        return build.stage(checkout, environment, phrase, **kwargs)


class BuildTests(unittest.TestCase):
    def test_lr1121_stages_wifi_fallback_and_default_band(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout, src = fake_checkout(directory)
            staged(checkout, build.DEFAULT_ENV)
            options = json.loads((src / "data/options.json").read_text())
            self.assertEqual(options["wifi-on-interval"], 60)
            self.assertEqual(options["sniffer-band"], 0)
            self.assertEqual(options["domain"], 2)
            self.assertEqual(len(options["uid"]), 6)
            self.assertNotEqual(options["flash-discriminator"], 0)
            self.assertIn("-DRegulatory_Domain_EU_868", (src / "user_defines.txt").read_text())
            self.assertIn("targets/t3s3_lr1121_sniffer.ini", (src / "platformio.ini").read_text())

    def test_heltec_v4_stages_sub_ghz_layout_without_band_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout, src = fake_checkout(directory)
            staged(checkout, "Heltec_V4_Sniffer_900_RX_USB", domain="FCC915")
            options = json.loads((src / "data/options.json").read_text())
            self.assertNotIn("sniffer-band", options)
            self.assertEqual(options["domain"], 1)
            self.assertEqual(options["wifi-on-interval"], 60)
            layout = json.loads((src / "data/hardware.json").read_text())
            self.assertEqual(layout, json.loads((FIRMWARE / "hardware/heltec_wifi_lora_32_v4_sniffer_rx.min.json").read_text()))
            self.assertEqual(layout["radio_dio1"], 14)
            self.assertEqual(layout["power_enable"], 7)
            defines = (src / "user_defines.txt").read_text()
            self.assertIn("-DRegulatory_Domain_FCC_915", defines)
            self.assertNotIn("Regulatory_Domain_EU_868", defines)
            self.assertIn("targets/heltec_v4_sniffer.ini", (src / "platformio.ini").read_text())
            self.assertTrue((src / "targets/heltec_v4_sniffer.ini").is_file())

    def test_unbound_image_has_no_uid_and_keeps_wifi_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout, src = fake_checkout(directory)
            staged(checkout, "Heltec_V4_Sniffer_900_RX_USB", phrase=None)
            options = json.loads((src / "data/options.json").read_text())
            self.assertNotIn("uid", options)
            self.assertEqual(options["wifi-on-interval"], 60)
            self.assertEqual(options["domain"], 2)
            self.assertNotEqual(options["flash-discriminator"], 0)
            defines = (src / "user_defines.txt").read_text()
            self.assertNotIn("MY_BINDING_PHRASE", defines)
            self.assertIn("-DRegulatory_Domain_EU_868", defines)
            # A second unbound stage keeps the same discriminator so a phrase
            # saved over Wi-Fi survives reflashing the same image.
            staged(checkout, "Heltec_V4_Sniffer_900_RX_USB", phrase=None)
            self.assertEqual(json.loads((src / "data/options.json").read_text())["flash-discriminator"],
                             options["flash-discriminator"])
            with self.assertRaises(ValueError):
                staged(checkout, "Heltec_V4_Sniffer_900_RX_USB", phrase="")

    def test_2400_target_ignores_the_sub_ghz_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout, src = fake_checkout(directory)
            staged(checkout, "T3S3_Sniffer_2400_RX_Serial", domain="AU915")
            options = json.loads((src / "data/options.json").read_text())
            self.assertEqual(options["domain"], 0)
            self.assertNotIn("sniffer-band", options)
            self.assertIn("-DRegulatory_Domain_ISM_2400", (src / "user_defines.txt").read_text())
            self.assertEqual((src / "data/hardware.json").read_text(),
                             (FIRMWARE / "hardware/lilygo_t3s3_sniffer_rx.min.json").read_text())

    def test_unknown_environment_and_plan_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout, _ = fake_checkout(directory)
            with self.assertRaises(ValueError):
                staged(checkout, "Heltec_V4_Sniffer_900_RX_Bluetooth")
            with self.assertRaises(ValueError):
                staged(checkout, "Unknown_Board_RX")
            with self.assertRaises(ValueError):
                staged(checkout, build.DEFAULT_ENV, domain="EU433")

    def test_every_target_ini_declares_its_environments(self):
        for prefix, (ini_name, layout, _, _) in build.TARGETS.items():
            text = (FIRMWARE / "targets" / ini_name).read_text(encoding="utf-8")
            self.assertIn(f"[env:{prefix}", text, ini_name)
            self.assertTrue((FIRMWARE / "hardware" / layout).is_file(), layout)

    def test_previous_band_filter_is_upgraded_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "common.cpp"
            marker = "    // Dual Band modes not supported for hardware with only a single LR1121"
            source.write_text('#include "OTA.h"\n' + integration.LEGACY_BAND_FILTER + marker)
            with redirect_stdout(io.StringIO()):
                updated = integration.apply_edits(source, integration.EXTRA_EDITS["src/common.cpp"])
                source.write_text(updated)
                self.assertEqual(integration.apply_edits(source, integration.EXTRA_EDITS["src/common.cpp"]), updated)
            self.assertNotIn("GHOST_LR1121_2400_ONLY", updated)
            self.assertEqual(updated.count("firmwareOptions.ghost_subghz"), 1)

    def test_firmware_and_filesystem_uploads_are_separate(self):
        argv = ["build.py", "checkout", "--phrase", "test-only", "--upload-port", "COM1"]
        with patch.object(build.sys, "argv", argv), \
                patch.object(build, "stage", return_value=Path("checkout/src")), \
                patch.object(build.importlib.util, "find_spec", return_value=object()), \
                patch.object(build.subprocess, "run") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(build.main(), 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertIn("upload", commands[0])
        self.assertNotIn("uploadfs", commands[0])
        self.assertIn("uploadfs", commands[1])
        self.assertNotIn("upload", commands[1])
        # The vendored ExpressLRS esptool must shadow any esptool 5.x in the
        # PlatformIO virtualenv, whose esptool.cmds lacks make_image.
        for call in run.call_args_list:
            python_path = call.kwargs["env"]["PYTHONPATH"].split(os.pathsep)
            self.assertEqual(Path(python_path[0]), Path("checkout/src/python/external/esptool"))

    def test_build_environment_prepends_vendored_esptool(self):
        with patch.dict(build.os.environ, {"PYTHONPATH": "existing"}):
            environment = build.build_environment(Path("elrs/src"))
        self.assertEqual(environment["PYTHONPATH"].split(os.pathsep),
                         [str(Path("elrs/src/python/external/esptool")), "existing"])
        with patch.dict(build.os.environ, {}, clear=True):
            environment = build.build_environment(Path("elrs/src"))
        self.assertEqual(environment["PYTHONPATH"], str(Path("elrs/src/python/external/esptool")))

    def test_filesystem_and_nobuild_keep_firmware(self):
        removal_guard = integration.EXTRA_EDITS["python/build_env_setup.py"][1][1]
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "test/firmware.bin"
            artifact.parent.mkdir()
            env = {"PROJECT_BUILD_DIR": directory, "PIOENV": "test", "PROGNAME": "firmware"}
            for targets in (["buildfs"], ["uploadfs"], ["uploadfsota"], ["nobuild", "upload"]):
                artifact.write_bytes(b"built firmware")
                exec(removal_guard, {"os": os, "env": env, "COMMAND_LINE_TARGETS": targets})
                self.assertEqual(artifact.read_bytes(), b"built firmware", str(targets))
            for targets in ([], ["upload"]):
                artifact.write_bytes(b"stale firmware")
                exec(removal_guard, {"os": os, "env": env, "COMMAND_LINE_TARGETS": targets})
                self.assertFalse(artifact.exists(), str(targets))

    def test_builder_patch_is_idempotent(self):
        edits = integration.EXTRA_EDITS["python/build_env_setup.py"]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "build_env_setup.py"
            source.write_text("\n\n".join(anchor for anchor, _ in edits), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                updated = integration.apply_edits(source, edits)
                source.write_text(updated, encoding="utf-8")
                self.assertEqual(integration.apply_edits(source, edits), updated)
            compile(updated, str(source), "exec")


if __name__ == "__main__":
    unittest.main()
