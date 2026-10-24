"""Reproducible ELRS hooks for the sniffer's boot-only configuration AP.

The access point opens once per boot when no link connects within the Wi-Fi
timeout. Because ExpressLRS stops the radio in Wi-Fi mode, an unattended access
point would silence the sniffer for good (a link that comes up a minute later
than the station is never received), so an automatically opened access point
that nobody joins restarts the station after GHOST_WIFI_IDLE_RESTART_MS and
listening resumes. Wi-Fi started on purpose, and access points with a client
connected, are left running.
"""


# Run both the connection latch and deadline decision on the RF/main core. The
# existing WiFi device then handles radio shutdown and web services on its core.
POLICY_CODE = """#if defined(GHOST_SNIFFER) && defined(TARGET_RX)
static bool ghostWifiEverConnected = false;
static volatile bool ghostForceAccessPoint = false;
static unsigned long ghostWifiIdleSince = 0;

// An automatic configuration access point that nobody joins must not keep the
// radio silent forever: restart after this long to resume listening (the boot
// deadline then re-arms). Explicitly started Wi-Fi is left alone.
#if !defined(GHOST_WIFI_IDLE_RESTART_MS)
#define GHOST_WIFI_IDLE_RESTART_MS 180000UL
#endif

void Ghost_WifiOnConnected()
{
  ghostWifiEverConnected = true;
}

bool Ghost_CheckBootWifi(uint32_t now)
{
  const int32_t interval = firmwareOptions.wifi_auto_on_interval;
  if (ghostWifiEverConnected || interval < 0 || now < (uint32_t)interval ||
      (connectionState != disconnected && connectionState != tentative))
    return false;

  // A boot fallback must remain reachable even with stored home-WiFi settings.
  ghostForceAccessPoint = true;
  setWifiUpdateMode();
  devicesTriggerEvent();
  return true;
}

void Ghost_WifiIdleCheck(unsigned long now)
{
  if (!ghostForceAccessPoint || rebootTime != 0 || wifiMode != WIFI_AP || WiFi.softAPgetStationNum() > 0)
  {
    ghostWifiIdleSince = 0;
    return;
  }
  if (ghostWifiIdleSince == 0)
  {
    ghostWifiIdleSince = now ? now : 1;
    return;
  }
  if (now - ghostWifiIdleSince >= GHOST_WIFI_IDLE_RESTART_MS)
  {
    DBGLN("Configuration access point unused, restarting to resume reception");
    rebootTime = now + 100;
  }
}
#endif

"""


EXTRA_EDITS = {
    "lib/WIFI/devWIFI.cpp": [
        ("/** Is this an IP? */", POLICY_CODE + "/** Is this an IP? */"),
        (
            "  if (station_ssid[0] == 0) {\n"
            "    changeTime = now;\n"
            "    changeMode = WIFI_AP;",
            "#if defined(GHOST_SNIFFER) && defined(TARGET_RX)\n"
            "  if (ghostForceAccessPoint || station_ssid[0] == 0) {\n"
            "#else\n"
            "  if (station_ssid[0] == 0) {\n"
            "#endif\n"
            "    changeTime = now;\n"
            "    changeMode = WIFI_AP;",
        ),
        (
            "  ipAddress.fromString(wifi_ap_address);\n"
            "  return firmwareOptions.wifi_auto_on_interval;",
            "  ipAddress.fromString(wifi_ap_address);\n"
            "#if defined(GHOST_SNIFFER) && defined(TARGET_RX)\n"
            "  // The RF loop owns the boot deadline; device startup time is irrelevant.\n"
            "  return DURATION_NEVER;\n"
            "#else\n"
            "  return firmwareOptions.wifi_auto_on_interval;\n"
            "#endif",
        ),
        (
            "    HandleWebUpdate();\n"
            "    HandleMSP2WIFI();",
            "    HandleWebUpdate();\n"
            "    HandleMSP2WIFI();\n"
            "#if defined(GHOST_SNIFFER) && defined(TARGET_RX)\n"
            "    Ghost_WifiIdleCheck(millis());\n"
            "#endif",
        ),
        (
            "  #elif defined(TARGET_RX)\n"
            "  if (firmwareOptions.wifi_auto_on_interval != -1 && !webserverPreventAutoStart && (connectionState == disconnected))",
            "  #elif defined(TARGET_RX) && !defined(GHOST_SNIFFER)\n"
            "  if (firmwareOptions.wifi_auto_on_interval != -1 && !webserverPreventAutoStart && (connectionState == disconnected))",
        ),
    ],
    "src/rx_main.cpp": [
        (
            '#include "devWIFI.h"',
            '#include "devWIFI.h"\n'
            "#if defined(GHOST_SNIFFER) && defined(HAS_WIFI)\n"
            "void Ghost_WifiOnConnected();\n"
            "bool Ghost_CheckBootWifi(uint32_t now);\n"
            "#endif",
        ),
        (
            "    connectionState = connected; //we got a packet, therefore no lost connection",
            "    connectionState = connected; //we got a packet, therefore no lost connection\n"
            "#if defined(GHOST_SNIFFER) && defined(HAS_WIFI)\n"
            "    Ghost_WifiOnConnected();\n"
            "#endif",
        ),
        (
            "bool ICACHE_RAM_ATTR RXdoneISR(SX12xxDriverCommon::rx_status const status)\n{",
            "bool ICACHE_RAM_ATTR RXdoneISR(SX12xxDriverCommon::rx_status const status)\n{\n"
            "#if defined(GHOST_SNIFFER)\n"
            "    // A pending packet must not restart acquisition during WiFi shutdown.\n"
            "    if (connectionState > MODE_STATES) return false;\n"
            "#endif",
        ),
        (
            "        GotConnection(now);\n"
            "    }\n\n"
            "    checkSendLinkStatsToFc(now);",
            "        GotConnection(now);\n"
            "    }\n\n"
            "#if defined(GHOST_SNIFFER) && defined(HAS_WIFI)\n"
            "    // Give a completed acquisition priority at the boot deadline.\n"
            "    if (Ghost_CheckBootWifi(now)) return;\n"
            "#endif\n\n"
            "    checkSendLinkStatsToFc(now);",
        ),
    ],
}

# The POLICY_CODE written by the previous version lacked the idle restart;
# integrate.py rewrites it in place.
LEGACY_POLICY_CODE = """#if defined(GHOST_SNIFFER) && defined(TARGET_RX)
static bool ghostWifiEverConnected = false;
static volatile bool ghostForceAccessPoint = false;

void Ghost_WifiOnConnected()
{
  ghostWifiEverConnected = true;
}

bool Ghost_CheckBootWifi(uint32_t now)
{
  const int32_t interval = firmwareOptions.wifi_auto_on_interval;
  if (ghostWifiEverConnected || interval < 0 || now < (uint32_t)interval ||
      (connectionState != disconnected && connectionState != tentative))
    return false;

  // A boot fallback must remain reachable even with stored home-WiFi settings.
  ghostForceAccessPoint = true;
  setWifiUpdateMode();
  devicesTriggerEvent();
  return true;
}
#endif

"""

UPGRADES = {
    "lib/WIFI/devWIFI.cpp": [(LEGACY_POLICY_CODE, POLICY_CODE)],
}
