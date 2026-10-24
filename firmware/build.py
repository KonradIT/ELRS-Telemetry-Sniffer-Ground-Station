#!/usr/bin/env python3
"""Stage and build the sniffer against a separate ExpressLRS 3.5.6 checkout.

Works on Windows, macOS and Linux. The binding phrase and derived UID are
written only to the supplied ExpressLRS checkout, never to this repository.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
DEFAULT_ENV = "T3S3_Sniffer_LR1121_RX_USB"


def stage(checkout, environment, phrase):
    src = checkout.resolve() / "src"
    if not (src / "src/rx_main.cpp").is_file():
        raise ValueError("Expected an ExpressLRS checkout containing src/src/rx_main.cpp")
    version = subprocess.check_output(
        ["git", "-C", str(checkout), "describe", "--tags", "--exact-match", "HEAD"],
        text=True,
    ).strip()
    if version != "3.5.6":
        raise ValueError(f"Expected ExpressLRS tag 3.5.6, found {version}")
    if not phrase or any(c in phrase for c in '\r\n"'):
        raise ValueError('Binding phrase must be nonempty and contain no newline or double quote')
    lr1121 = "_LR1121_" in environment
    target = HERE / "targets" / ("t3s3_lr1121_sniffer.ini" if lr1121 else "t3s3_sniffer.ini")
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
    # LR1121 requires a primary sub-GHz domain even when the secondary
    # 2.4 GHz band is selected. Both can be changed through the web UI.
    text = re.sub(r"(?m)^\s*-DRegulatory_Domain_.*$", "", text)
    text = re.sub(r"(?m)^\s*#?\s*-DMY_BINDING_PHRASE=.*$", "", text)
    text = re.sub(r"(?m)^\s*-DAUTO_WIFI_ON_INTERVAL=.*$", "", text)
    domain = "EU_868" if lr1121 else "ISM_2400"
    text = text.rstrip() + f'\n-DRegulatory_Domain_{domain}\n-DMY_BINDING_PHRASE="{phrase}"\n'
    defines.write_text(text, encoding="utf-8")

    data = src / "data"
    data.mkdir(exist_ok=True)
    layout = "lilygo_t3s3_lr1121_sniffer_rx.min.json" if lr1121 else "lilygo_t3s3_sniffer_rx.min.json"
    shutil.copy2(HERE / "hardware" / layout, data / "hardware.json")
    digest = hashlib.md5(f'-DMY_BINDING_PHRASE="{phrase}"'.encode()).digest()
    options = {
        "uid": list(digest[:6]),
        "flash-discriminator": int.from_bytes(digest[6:10], "little") or 1,
        "wifi-on-interval": 60,
        "domain": 2 if lr1121 else 0,
    }
    if lr1121:
        options["sniffer-band"] = 0
    (data / "options.json").write_text(json.dumps(options), encoding="utf-8")
    print(f"Staged {environment}, hardware={layout}, UID suffix=" + ".".join(map(str, digest[3:6])))
    return src


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path, help="Separate ExpressLRS 3.5.6 checkout")
    parser.add_argument("--env", default=DEFAULT_ENV)
    parser.add_argument("--phrase", required=True)
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--upload-port", help="Also flash firmware AND the hardware/options filesystem")
    args = parser.parse_args()
    try:
        src = stage(args.checkout, args.env, args.phrase)
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
        # espressif32 6.4.0 selects the filesystem image for *all* targets if
        # uploadfs/buildfs is present. Combining upload + uploadfs therefore
        # uploads only SPIFFS, never the firmware. Keep these calls separate;
        # each upload target already builds its corresponding image.
        if args.upload_port:
            for target in ("upload", "uploadfs"):
                subprocess.run(build + ["-t", target, "--upload-port", args.upload_port], cwd=src, check=True)
        else:
            subprocess.run(build, cwd=src, check=True)
            subprocess.run(build + ["-t", "buildfs"], cwd=src, check=True)
        print(f"Firmware: {src / '.pio/build' / args.env / 'firmware.bin'}")
        print("First flash requires both firmware and filesystem (use --upload-port).")
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
