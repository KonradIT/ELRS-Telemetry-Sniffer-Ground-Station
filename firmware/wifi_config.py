"""Reproducible ELRS 3.5.6 options and web UI patches for the sniffer targets.

``isSniffer`` marks every receive-only sniffer build (SX1280, LR1121 and
SX126x): its options page defaults the Wi-Fi timeout to 60 seconds, treats a
blank timeout as disabled, hides the 433 MHz plans and labels the domain select
as the transmitter's frequency plan. ``hasSnifferBand`` is true only for the
LR1121 target, the single radio here that can choose between 2.4 GHz and
sub-GHz reception; its persisted ``sniffer-band`` is 0 for 2.4 GHz or 1 for
sub-GHz. Every sniffer build stores that key when it saves settings (other
radios ignore its value) because it also marks the new settings format: legacy
sniffer settings without the key gain the 60-second Wi-Fi recovery timeout,
while an explicit -1 timeout saved alongside the key remains disabled.

``UPGRADES`` rewrites text produced by earlier versions of these patches so an
already-integrated checkout converges on the current edits.
"""

SNIFFER_FLAG = (
    "bool(re.search(\n"
    "        r'(?:^|\\s)-D\\s*GHOST_SNIFFER(?:=1)?(?:\\s|$)', ' '.join(str(flag) for flag in env['BUILD_FLAGS'])))"
)

SAVE_WIFI = """#if defined(GHOST_SNIFFER)
    // Preserve an explicitly disabled timeout when serializing settings.
    doc["wifi-on-interval"] = firmwareOptions.wifi_auto_on_interval == -1
        ? -1 : firmwareOptions.wifi_auto_on_interval / 1000;
#else
    if (firmwareOptions.wifi_auto_on_interval != -1)
    {
        doc["wifi-on-interval"] = firmwareOptions.wifi_auto_on_interval / 1000;
    }
#endif"""

LOAD_WIFI = """#if defined(GHOST_SNIFFER)
    int32_t wifiInterval = doc["wifi-on-interval"] | 60;
    // Old sniffer images disabled Wi-Fi and had no band setting.
    if (!doc.containsKey("sniffer-band") && wifiInterval == -1)
        wifiInterval = 60;
    if (wifiInterval < -1 || wifiInterval > 2147483)
        wifiInterval = 60;
#else
    int32_t wifiInterval = doc["wifi-on-interval"] | -1;
#endif
    firmwareOptions.wifi_auto_on_interval = wifiInterval == -1 ? -1 : wifiInterval * 1000;"""

SAVE_BAND = """#if defined(GHOST_SNIFFER)
    doc["sniffer-band"] = firmwareOptions.ghost_subghz ? 1 : 0;
#endif"""

LEGACY_LOAD_BAND = """#if defined(GHOST_SNIFFER)
    firmwareOptions.ghost_subghz = doc["sniffer-band"].is<int>()
        && doc["sniffer-band"].as<int>() == 1;
#endif
#if defined(GHOST_SNIFFER) && defined(RADIO_LR1121)
    // Only the 830-945 MHz plans are supported by this board's RF path.
    const int snifferDomain = doc["domain"].is<int>() ? doc["domain"].as<int>() : 2;
    firmwareOptions.domain = snifferDomain >= 0 && snifferDomain <= 3 ? snifferDomain : 2;
#else
    firmwareOptions.domain = doc["domain"] | 0;
#endif"""

LOAD_BAND = """#if defined(GHOST_SNIFFER)
    firmwareOptions.ghost_subghz = doc["sniffer-band"].is<int>()
        && doc["sniffer-band"].as<int>() == 1;
#endif
#if defined(GHOST_SNIFFER) && (defined(RADIO_LR1121) || defined(RADIO_SX126X))
    // Only the 830-945 MHz plans are supported by these boards' RF paths.
    const int snifferDomain = doc["domain"].is<int>() ? doc["domain"].as<int>() : 2;
    firmwareOptions.domain = snifferDomain >= 0 && snifferDomain <= 3 ? snifferDomain : 2;
#else
    firmwareOptions.domain = doc["domain"] | 0;
#endif"""

LEGACY_VALIDATE_SETTINGS = """#if defined(GHOST_SNIFFER) && defined(RADIO_LR1121)
static const char *Ghost_ValidateSettings(JsonVariant &json)
{
  if (!json.is<JsonObject>())
    return "Settings must be a JSON object.";
  if (json.containsKey("sniffer-band") &&
      (!json["sniffer-band"].is<int>() || json["sniffer-band"].as<int>() < 0 || json["sniffer-band"].as<int>() > 1))
    return "Choose 2.4 GHz or Sub-1 GHz.";
  if (json.containsKey("domain") &&
      (!json["domain"].is<int>() || json["domain"].as<int>() < 0 || json["domain"].as<int>() > 3))
    return "Choose AU915, FCC915, EU868 or IN866.";
  if (json.containsKey("wifi-on-interval") &&
      (!json["wifi-on-interval"].is<int32_t>() || json["wifi-on-interval"].as<int32_t>() < -1 || json["wifi-on-interval"].as<int32_t>() > 2147483))
    return "Wi-Fi timeout must be a nonnegative number of seconds, or -1 to disable.";
  // Retain current selections when a client omits optional fields.
  if (!json.containsKey("sniffer-band"))
    json["sniffer-band"] = firmwareOptions.ghost_subghz ? 1 : 0;
  if (!json.containsKey("domain"))
    json["domain"] = firmwareOptions.domain;
  if (!json.containsKey("wifi-on-interval"))
    json["wifi-on-interval"] = firmwareOptions.wifi_auto_on_interval == -1
        ? -1 : firmwareOptions.wifi_auto_on_interval / 1000;
  return nullptr;
}
#endif

"""

VALIDATE_SETTINGS = """#if defined(GHOST_SNIFFER)
static const char *Ghost_ValidateSettings(JsonVariant &json)
{
  if (!json.is<JsonObject>())
    return "Settings must be a JSON object.";
#if defined(RADIO_LR1121)
  if (json.containsKey("sniffer-band") &&
      (!json["sniffer-band"].is<int>() || json["sniffer-band"].as<int>() < 0 || json["sniffer-band"].as<int>() > 1))
    return "Choose 2.4 GHz or Sub-1 GHz.";
#endif
#if defined(RADIO_LR1121) || defined(RADIO_SX126X)
  if (json.containsKey("domain") &&
      (!json["domain"].is<int>() || json["domain"].as<int>() < 0 || json["domain"].as<int>() > 3))
    return "Choose AU915, FCC915, EU868 or IN866.";
#endif
  if (json.containsKey("wifi-on-interval") &&
      (!json["wifi-on-interval"].is<int32_t>() || json["wifi-on-interval"].as<int32_t>() < -1 || json["wifi-on-interval"].as<int32_t>() > 2147483))
    return "Wi-Fi timeout must be a nonnegative number of seconds, or -1 to disable.";
  // Retain current selections when a client omits optional fields. The band
  // key also marks settings written by this firmware, so a saved -1 timeout
  // is not mistaken for a legacy image's disabled default.
  if (!json.containsKey("sniffer-band"))
    json["sniffer-band"] = firmwareOptions.ghost_subghz ? 1 : 0;
  if (!json.containsKey("domain"))
    json["domain"] = firmwareOptions.domain;
  if (!json.containsKey("wifi-on-interval"))
    json["wifi-on-interval"] = firmwareOptions.wifi_auto_on_interval == -1
        ? -1 : firmwareOptions.wifi_auto_on_interval / 1000;
  return nullptr;
}
#endif

"""

LEGACY_VALIDATE_CALL = (
    "#if defined(GHOST_SNIFFER) && defined(RADIO_LR1121)\n"
    "  if (const char *error = Ghost_ValidateSettings(json)) {\n"
    "    request->send(400, \"text/plain\", error);\n"
    "    return;\n"
    "  }\n"
    "#endif\n"
    "  File file = SPIFFS.open(\"/options.json\", \"w\");\n  serializeJson(json, file);"
)

VALIDATE_CALL = (
    "#if defined(GHOST_SNIFFER)\n"
    "  if (const char *error = Ghost_ValidateSettings(json)) {\n"
    "    request->send(400, \"text/plain\", error);\n"
    "    return;\n"
    "  }\n"
    "#endif\n"
    "  File file = SPIFFS.open(\"/options.json\", \"w\");\n  serializeJson(json, file);"
)

LEGACY_HTML_FLAGS = (
    "            'isTX': isTX,\n"
    "            'isSniffer': not isTX and chip == 'LR1121' and " + SNIFFER_FLAG + ","
)

HTML_FLAGS = (
    "            'isTX': isTX,\n"
    "            'isSniffer': is_sniffer,\n"
    "            'hasSnifferBand': is_sniffer and chip == 'LR1121',"
)

HTML_SNIFFER_FLAG = (
    "    is_sniffer = not isTX and " + SNIFFER_FLAG + "\n"
    "    if 'ESP8285' in env['PIOENV']:"
)

LEGACY_INDEX_REQUIRE = "@@require(PLATFORM, VERSION, isTX, hasSubGHz, is8285, isSniffer)"
INDEX_REQUIRE = "@@require(PLATFORM, VERSION, isTX, hasSubGHz, is8285, isSniffer, hasSnifferBand)"
LEGACY_SCAN_REQUIRE = "@@require(PLATFORM, isTX, is8285, isSniffer)"
SCAN_REQUIRE = "@@require(PLATFORM, isTX, is8285, hasSubGHz, isSniffer, hasSnifferBand)"

BAND_FORM_BODY = """                        <div class="mui-select">
                            <select id="sniffer-band" name="sniffer-band">
                                <option value="0">2.4 GHz</option>
                                <option value="1">Sub-1 GHz</option>
                            </select>
                            <label for="sniffer-band">Receive band</label>
                        </div>
                        <p>Receive telemetry from one band at a time. Use the antenna connection for the selected band. Save and reboot to apply changes.</p>
@@end
"""
LEGACY_BAND_FORM = "@@if isSniffer:\n" + BAND_FORM_BODY
BAND_FORM = "@@if hasSnifferBand:\n" + BAND_FORM_BODY

BAND_SCRIPT_BODY = """function updateSnifferBand() {
  _('sniffer-domain').style.display = _('sniffer-band').value === '1' ? 'block' : 'none';
}
_('sniffer-band').addEventListener('change', updateSnifferBand);
@@end

"""
LEGACY_BAND_SCRIPT = "@@if isSniffer:\n" + BAND_SCRIPT_BODY
BAND_SCRIPT = "@@if hasSnifferBand:\n" + BAND_SCRIPT_BODY

LEGACY_OPTION_DEFAULTS = (
    "function updateOptions(data) {\n"
    "@@if isSniffer:\n"
    "  _('sniffer-band').value = '0';\n"
    "  _('domain').value = '2';\n"
    "  _('wifi-on-interval').value = '';\n"
    "@@end"
)

OPTION_DEFAULTS = (
    "function updateOptions(data) {\n"
    "@@if isSniffer:\n"
    "  _('wifi-on-interval').value = '';\n"
    "@@if hasSubGHz:\n"
    "  _('domain').value = '2';\n"
    "@@end\n"
    "@@if hasSnifferBand:\n"
    "  _('sniffer-band').value = '0';\n"
    "@@end\n"
    "@@end"
)

LEGACY_BAND_REFRESH = "@@if isSniffer:\n  updateSnifferBand();\n@@end\n"
BAND_REFRESH = "@@if hasSnifferBand:\n  updateSnifferBand();\n@@end\n"

EXTRA_EDITS = {
    "lib/OPTIONS/options.h": [
        ("    uint8_t     domain;         // depends on radio chip",
         "    uint8_t     domain;         // depends on radio chip\n"
         "#if defined(GHOST_SNIFFER)\n"
         "    bool        ghost_subghz;   // false: 2.4 GHz, true: sub-GHz\n"
         "#endif"),
    ],
    "lib/OPTIONS/options.cpp": [
        ("    if (firmwareOptions.wifi_auto_on_interval != -1)\n"
         "    {\n"
         "        doc[\"wifi-on-interval\"] = firmwareOptions.wifi_auto_on_interval / 1000;\n"
         "    }", SAVE_WIFI),
        ("    int32_t wifiInterval = doc[\"wifi-on-interval\"] | -1;\n"
         "    firmwareOptions.wifi_auto_on_interval = wifiInterval == -1 ? -1 : wifiInterval * 1000;", LOAD_WIFI),
        ("    doc[\"domain\"] = firmwareOptions.domain;\n    doc[\"customised\"] = customised;",
         "    doc[\"domain\"] = firmwareOptions.domain;\n" + SAVE_BAND + "\n    doc[\"customised\"] = customised;"),
        ("    firmwareOptions.domain = doc[\"domain\"] | 0;", LOAD_BAND),
    ],
    "lib/WIFI/devWIFI.cpp": [
        ("static void UpdateSettings(AsyncWebServerRequest *request, JsonVariant &json)",
         VALIDATE_SETTINGS + "static void UpdateSettings(AsyncWebServerRequest *request, JsonVariant &json)"),
        ("  File file = SPIFFS.open(\"/options.json\", \"w\");\n  serializeJson(json, file);", VALIDATE_CALL),
    ],
    "python/build_html.py": [
        ("    if 'ESP8285' in env['PIOENV']:", HTML_SNIFFER_FLAG),
        ("            'isTX': isTX,", HTML_FLAGS),
    ],
    "html/index.html": [
        ("@@require(PLATFORM, VERSION, isTX, hasSubGHz, is8285)", INDEX_REQUIRE),
        ("@@if hasSubGHz:\n", BAND_FORM + "@@if hasSubGHz:\n"),
        ("\t\t\t\t\t\t<div class=\"mui-select\">\n\t\t\t\t\t\t\t<select id='domain' name='domain'>",
         "\t\t\t\t\t\t<div class=\"mui-select\" id=\"sniffer-domain\">\n\t\t\t\t\t\t\t<select id='domain' name='domain'>"),
        ("\t\t\t\t\t\t\t\t<option value='4'>AU433</option>\n"
         "\t\t\t\t\t\t\t\t<option value='5'>EU433</option>\n"
         "\t\t\t\t\t\t\t\t<option value='6'>US433</option>\n"
         "\t\t\t\t\t\t\t\t<option value='7'>US433-Wide</option>",
         "@@if not isSniffer:\n"
         "\t\t\t\t\t\t\t\t<option value='4'>AU433</option>\n"
         "\t\t\t\t\t\t\t\t<option value='5'>EU433</option>\n"
         "\t\t\t\t\t\t\t\t<option value='6'>US433</option>\n"
         "\t\t\t\t\t\t\t\t<option value='7'>US433-Wide</option>\n"
         "@@end"),
        ("\t\t\t\t\t\t\t<label for=\"domain\">Regulatory domain</label>",
         "@@if isSniffer:\n"
         "                            <label for=\"domain\">Frequency plan (match your transmitter)</label>\n"
         "@@else:\n"
         "\t\t\t\t\t\t\t<label for=\"domain\">Regulatory domain</label>\n"
         "@@end"),
    ],
    "html/scan.js": [
        ("@@require(PLATFORM, isTX, is8285)", SCAN_REQUIRE),
        ("  formObject['customised'] = true;",
         "  formObject['customised'] = true;\n"
         "@@if isSniffer:\n"
         "  // A blank timeout explicitly disables automatic Wi-Fi.\n"
         "  if (!String(formObject['wifi-on-interval'] || '').trim()) formObject['wifi-on-interval'] = -1;\n"
         "@@end"),
        ("function updateOptions(data) {", BAND_SCRIPT + OPTION_DEFAULTS),
        ("  if (data['wifi-ssid']) _('homenet').textContent = data['wifi-ssid'];",
         BAND_REFRESH + "  if (data['wifi-ssid']) _('homenet').textContent = data['wifi-ssid'];"),
    ],
}

# Earlier edit text -> current edit text, applied before the anchors above.
UPGRADES = {
    "lib/OPTIONS/options.cpp": [(LEGACY_LOAD_BAND, LOAD_BAND)],
    "lib/WIFI/devWIFI.cpp": [
        (LEGACY_VALIDATE_SETTINGS, VALIDATE_SETTINGS),
        (LEGACY_VALIDATE_CALL, VALIDATE_CALL),
    ],
    "python/build_html.py": [(LEGACY_HTML_FLAGS, HTML_FLAGS)],
    "html/index.html": [
        (LEGACY_INDEX_REQUIRE, INDEX_REQUIRE),
        (LEGACY_BAND_FORM, BAND_FORM),
    ],
    "html/scan.js": [
        (LEGACY_SCAN_REQUIRE, SCAN_REQUIRE),
        (LEGACY_BAND_SCRIPT, BAND_SCRIPT),
        (LEGACY_OPTION_DEFAULTS, OPTION_DEFAULTS),
        (LEGACY_BAND_REFRESH, BAND_REFRESH),
    ],
}
