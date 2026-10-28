#!/usr/bin/env python3
"""Stage and build the sniffer against a separate ExpressLRS 3.5.6 checkout.

Works on Windows, macOS and Linux. The binding phrase and derived UID are
written only to the supplied ExpressLRS checkout, never to this repository.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
DEFAULT_ENV = "T3S3_Sniffer_LR1121_RX_USB"
DEFAULT_DOMAIN = "EU868"
# Sub-GHz frequency plans: ExpressLRS regulatory domain define and the index
# stored in options.json. The web UI can change the plan afterwards.
DOMAINS = {
    "AU915": ("AU_915", 0),
    "FCC915": ("FCC_915", 1),
    "EU868": ("EU_868", 2),
    "IN866": ("IN_866", 3),
}
# Environment prefix -> (target INI, staged hardware layout, sub-GHz radio,
# selectable 2.4 GHz/sub-GHz band). integrate.py installs any board definition
# or radio driver a target needs.
TARGETS = {
    "T3S3_Sniffer_LR1121_": ("t3s3_lr1121_sniffer.ini", "lilygo_t3s3_lr1121_sniffer_rx.min.json", True, True),
    "Heltec_V4_Sniffer_": ("heltec_v4_sniffer.ini", "heltec_wifi_lora_32_v4_sniffer_rx.min.json", True, False),
    "T3S3_Sniffer_2400_": ("t3s3_sniffer.ini", "lilygo_t3s3_sniffer_rx.min.json", False, False),
}


def target_for(environment):
    for prefix, target in TARGETS.items():
        if environment.startswith(prefix):
            return target
    raise ValueError(f"Unknown sniffer environment: {environment}")


def stage(checkout, environment, phrase, domain=DEFAULT_DOMAIN):
    """Stage ``environment``; ``phrase=None`` stages an unbound receiver.

    An unbound receiver carries no UID: it boots into ExpressLRS binding mode
    (listening only, never transmitting) and, with no link after the Wi-Fi
    timeout, opens the configuration access point where the binding phrase is
    entered and saved to the device configuration.
    """
    src = checkout.resolve() / "src"
    if not (src / "src/rx_main.cpp").is_file():
        raise ValueError("Expected an ExpressLRS checkout containing src/src/rx_main.cpp")
    version = subprocess.check_output(
        ["git", "-C", str(checkout), "describe", "--tags", "--exact-match", "HEAD"],
        text=True,
    ).strip()
    if version != "3.5.6":
        raise ValueError(f"Expected ExpressLRS tag 3.5.6, found {version}")
    if phrase is not None and (not phrase or any(c in phrase for c in '\r\n"')):
        raise ValueError('Binding phrase must be nonempty and contain no newline or double quote')
    if domain not in DOMAINS:
        raise ValueError(f"Unknown frequency plan: {domain}")
    ini_name, layout, sub_ghz, has_band = target_for(environment)
    target = HERE / "targets" / ini_name
    if f"[env:{environment}]" not in target.read_text(encoding="utf-8"):
        raise ValueError(f"Unknown sniffer environment: {environment}")

    subprocess.run([sys.executable, str(HERE / "integrate.py"), str(src)], check=True)
    # ELRS generates this header during its first build. Include its path in
    # PlatformIO's initial project checksum, otherwise the next invocation
    # treats it as a new source file and deletes every environment's build.
    web_content = src / "include/WebContent.h"
    if not web_content.exists():
        web_content.touch()
    shutil.copy2(target, src / "targets" / target.name)
    ini = src / "platformio.ini"
    text = ini.read_text(encoding="utf-8")
    include = f"targets/{target.name}"
    if include not in text:
        anchor = "\ttargets/common.ini\n"
        if text.count(anchor) != 1:
            raise ValueError("Cannot find the PlatformIO extra_configs insertion point")
        ini.write_text(text.replace(anchor, anchor + f"\t{include}\n", 1), encoding="utf-8")

    defines = src / "user_defines.txt"
    text = defines.read_text(encoding="utf-8") if defines.exists() else ""
    # Sub-GHz radios require a primary domain even when the LR1121's secondary
    # 2.4 GHz band is selected. Both can be changed through the web UI.
    text = re.sub(r"(?m)^\s*-DRegulatory_Domain_.*$", "", text)
    text = re.sub(r"(?m)^\s*#?\s*-DMY_BINDING_PHRASE=.*$", "", text)
    text = re.sub(r"(?m)^\s*-DAUTO_WIFI_ON_INTERVAL=.*$", "", text)
    domain_define, domain_index = DOMAINS[domain] if sub_ghz else ("ISM_2400", 0)
    text = text.rstrip() + f"\n-DRegulatory_Domain_{domain_define}\n"
    if phrase is not None:
        text += f'-DMY_BINDING_PHRASE="{phrase}"\n'
    defines.write_text(text, encoding="utf-8")

    data = src / "data"
    data.mkdir(exist_ok=True)
    shutil.copy2(HERE / "hardware" / layout, data / "hardware.json")
    # ExpressLRS copies the UID from these options into its stored config only
    # when the discriminator differs from the saved one, so derive it from the
    # phrase (stable per phrase, never 0 == unset). An unbound image uses a
    # fixed discriminator so a phrase saved over Wi-Fi survives a reflash.
    digest = hashlib.md5(f'-DMY_BINDING_PHRASE="{phrase}"'.encode() if phrase is not None else b"unbound").digest()
    options = {
        "flash-discriminator": int.from_bytes(digest[6:10], "little") or 1,
        "wifi-on-interval": 60,
        "domain": domain_index,
    }
    if phrase is not None:
        options["uid"] = list(digest[:6])
    if has_band:
        options["sniffer-band"] = 0
    (data / "options.json").write_text(json.dumps(options), encoding="utf-8")
    plan = domain if sub_ghz else "ISM2G4"
    binding = "UID suffix=" + ".".join(map(str, digest[3:6])) if phrase is not None else "unbound (set the phrase over Wi-Fi)"
    print(f"Staged {environment}, hardware={layout}, plan={plan}, {binding}")
    return src


def build_environment(src):
    """Environment for PlatformIO: the ExpressLRS build scripts import their
    vendored esptool 4.x by its bare package name, which an esptool 5.x
    installed in the PlatformIO virtualenv would otherwise shadow (missing
    ``esptool.cmds.make_image``). Put the vendored copy first."""
    environment = dict(os.environ)
    vendored = str(src / "python" / "external" / "esptool")
    current = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = vendored + (os.pathsep + current if current else "")
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path, help="Separate ExpressLRS 3.5.6 checkout")
    parser.add_argument("--env", default=DEFAULT_ENV)
    binding = parser.add_mutually_exclusive_group(required=True)
    binding.add_argument("--phrase", help="Binding phrase of the link to monitor")
    binding.add_argument("--unbound", action="store_true",
                         help="Flash without a UID; enter the phrase on the Wi-Fi configuration page that opens "
                              "after the Wi-Fi timeout (60 s without a link)")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN, choices=sorted(DOMAINS),
                        help="Sub-GHz frequency plan staged as the default (ignored by 2.4 GHz targets)")
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--upload-port", help="Also flash firmware AND the hardware/options filesystem")
    args = parser.parse_args()
    try:
        src = stage(args.checkout, args.env, None if args.unbound else args.phrase, args.domain)
        if args.stage_only:
            return 0
        # Prefer this interpreter's PlatformIO (e.g. an activated virtualenv),
        # rather than an unrelated or broken system launcher on PATH.
        if importlib.util.find_spec("platformio"):
            command = [sys.executable, "-m", "platformio"]
        else:
            pio = shutil.which("pio") or shutil.which("platformio")
            if not pio:
                raise ValueError("Install PlatformIO first: python -m pip install platformio")
            command = [pio]
        build = command + ["run", "-e", args.env]
        environment = build_environment(src)
        # espressif32 6.4.0 selects the filesystem image for *all* targets if
        # uploadfs/buildfs is present. Combining upload + uploadfs therefore
        # uploads only SPIFFS, never the firmware. Keep these calls separate;
        # each upload target already builds its corresponding image.
        if args.upload_port:
            for target in ("upload", "uploadfs"):
                subprocess.run(build + ["-t", target, "--upload-port", args.upload_port], cwd=src, env=environment, check=True)
        else:
            subprocess.run(build, cwd=src, env=environment, check=True)
            subprocess.run(build + ["-t", "buildfs"], cwd=src, env=environment, check=True)
        print(f"Firmware: {src / '.pio/build' / args.env / 'firmware.bin'}")
        print("First flash requires both firmware and filesystem (use --upload-port).")
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
