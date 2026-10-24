"""Exercise the actual generated C++ boot policy without a radio or ESP32."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


FIRMWARE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wifi_fallback", FIRMWARE / "wifi_fallback.py")
fallback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fallback)


class WifiFallbackTests(unittest.TestCase):
    def test_boot_deadline_connection_latch_and_idle_restart(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("A native C++ compiler is required for the firmware policy test")

        ap_branch = fallback.EXTRA_EDITS["lib/WIFI/devWIFI.cpp"][1][1]
        harness = r"""
#include <cassert>
#include <cstdint>
#define GHOST_SNIFFER 1
#define TARGET_RX 1
#define DBGLN(...)
enum State { connected, tentative, disconnected, wifiUpdate, serialUpdate };
State connectionState = disconnected;
struct { int32_t wifi_auto_on_interval; } firmwareOptions = {60000};
unsigned events = 0;
void setWifiUpdateMode() { connectionState = wifiUpdate; }
void devicesTriggerEvent() { ++events; }
enum WiFiMode_t { WIFI_OFF, WIFI_AP, WIFI_STA };
volatile WiFiMode_t wifiMode = WIFI_OFF;
unsigned long rebootTime = 0;
struct { unsigned stations = 0; unsigned softAPgetStationNum() { return stations; } } WiFi;
""" + fallback.POLICY_CODE + r"""
WiFiMode_t changeMode = WIFI_STA;
unsigned long changeTime = 0;
char station_ssid[33] = "configured home network";
void chooseMode(unsigned long now) {
""" + ap_branch + r"""
  } else { changeMode = WIFI_STA; }
}
void reset() {
  ghostWifiEverConnected = false;
  ghostForceAccessPoint = false;
  ghostWifiIdleSince = 0;
  connectionState = disconnected;
  firmwareOptions.wifi_auto_on_interval = 60000;
  events = 0;
  wifiMode = WIFI_OFF;
  rebootTime = 0;
  WiFi.stations = 0;
}
int main() {
  // The absolute boot deadline is not restarted by acquisition/rate changes.
  assert(!Ghost_CheckBootWifi(30000));
  connectionState = tentative;
  assert(!Ghost_CheckBootWifi(59999));
  assert(Ghost_CheckBootWifi(60000));
  assert(connectionState == wifiUpdate && events == 1 && ghostForceAccessPoint);
  assert(!Ghost_CheckBootWifi(60001) && events == 1);
  chooseMode(60000);
  assert(changeMode == WIFI_AP && station_ssid[0] != 0);

  reset();
  chooseMode(0);
  assert(changeMode == WIFI_STA); // Manual WiFi keeps the stored home network.
  assert(Ghost_CheckBootWifi(60000)); // Plain disconnected also falls back.

  reset();
  connectionState = connected;
  Ghost_WifiOnConnected();
  assert(!Ghost_CheckBootWifi(60000)); // Connection wins at the deadline.
  connectionState = disconnected;
  assert(!Ghost_CheckBootWifi(120000)); // A later loss never re-arms fallback.
  connectionState = tentative;
  assert(!Ghost_CheckBootWifi(240000) && events == 0);

  reset(); // Reboot re-arms the policy.
  assert(Ghost_CheckBootWifi(60000));
  reset();
  firmwareOptions.wifi_auto_on_interval = -1;
  assert(!Ghost_CheckBootWifi(120000)); // Explicitly disabled remains disabled.
  reset();
  connectionState = serialUpdate;
  assert(!Ghost_CheckBootWifi(120000) && events == 0);

  // An automatic access point nobody joins restarts the station after the
  // idle window so the radio listens again; the window starts once the AP is up.
  reset();
  assert(Ghost_CheckBootWifi(60000));
  Ghost_WifiIdleCheck(60100);            // mode change still pending: not idle yet
  assert(rebootTime == 0);
  wifiMode = WIFI_AP;
  Ghost_WifiIdleCheck(61000);            // idle window opens
  Ghost_WifiIdleCheck(61000 + GHOST_WIFI_IDLE_RESTART_MS - 1);
  assert(rebootTime == 0);
  Ghost_WifiIdleCheck(61000 + GHOST_WIFI_IDLE_RESTART_MS);
  assert(rebootTime == 61000 + GHOST_WIFI_IDLE_RESTART_MS + 100);
  unsigned long scheduled = rebootTime;
  Ghost_WifiIdleCheck(scheduled + 50);   // already scheduled: left alone
  assert(rebootTime == scheduled);

  // A connected client keeps the access point, and the window restarts after it leaves.
  reset();
  assert(Ghost_CheckBootWifi(60000));
  wifiMode = WIFI_AP;
  Ghost_WifiIdleCheck(61000);
  WiFi.stations = 1;
  Ghost_WifiIdleCheck(61000 + GHOST_WIFI_IDLE_RESTART_MS);
  Ghost_WifiIdleCheck(61000 + 10 * GHOST_WIFI_IDLE_RESTART_MS);
  assert(rebootTime == 0);
  WiFi.stations = 0;
  Ghost_WifiIdleCheck(1000000);          // window restarts from here
  Ghost_WifiIdleCheck(1000000 + GHOST_WIFI_IDLE_RESTART_MS - 1);
  assert(rebootTime == 0);
  Ghost_WifiIdleCheck(1000000 + GHOST_WIFI_IDLE_RESTART_MS);
  assert(rebootTime != 0);

  // Wi-Fi started on purpose (web page, button) never restarts on its own.
  reset();
  connectionState = wifiUpdate;
  wifiMode = WIFI_AP;
  Ghost_WifiIdleCheck(1000);
  Ghost_WifiIdleCheck(1000 + 10 * GHOST_WIFI_IDLE_RESTART_MS);
  assert(rebootTime == 0);

  // A forced access point that joined the home network instead is left alone too.
  reset();
  assert(Ghost_CheckBootWifi(60000));
  wifiMode = WIFI_STA;
  Ghost_WifiIdleCheck(61000);
  Ghost_WifiIdleCheck(61000 + 10 * GHOST_WIFI_IDLE_RESTART_MS);
  assert(rebootTime == 0);

  // millis() of 0 still starts the window.
  reset();
  assert(Ghost_CheckBootWifi(60000));
  wifiMode = WIFI_AP;
  Ghost_WifiIdleCheck(0);
  Ghost_WifiIdleCheck(GHOST_WIFI_IDLE_RESTART_MS + 1);
  assert(rebootTime != 0);
}
"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "policy.cpp"
            executable = Path(directory) / "policy.exe"
            source.write_text(harness, encoding="utf-8")
            subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 str(source), "-o", str(executable)], check=True, capture_output=True,
            )
            subprocess.run([str(executable)], check=True, capture_output=True)

    def test_patches_are_unique_and_idempotent(self):
        for path, edits in fallback.EXTRA_EDITS.items():
            text = "\n\n".join(anchor for anchor, _ in edits)
            for anchor, replacement in edits:
                self.assertEqual(text.count(anchor), 1, path)
                text = text.replace(anchor, replacement)
            updated = text
            for anchor, replacement in edits:
                if replacement not in text:
                    text = text.replace(anchor, replacement)
            self.assertEqual(text, updated, path)

    def test_legacy_policy_upgrades_to_idle_restart(self):
        legacy, current = fallback.UPGRADES["lib/WIFI/devWIFI.cpp"][0]
        self.assertNotIn("Ghost_WifiIdleCheck", legacy)
        self.assertIn("Ghost_WifiIdleCheck", current)
        self.assertEqual(fallback.EXTRA_EDITS["lib/WIFI/devWIFI.cpp"][0][1], current + "/** Is this an IP? */")


if __name__ == "__main__":
    unittest.main()
