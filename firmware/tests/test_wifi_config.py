"""Exercise ELRS option migration, HTTP validation and rendered configuration UI.

Set ELRS_SRC to an ExpressLRS 3.5.6 src checkout to run the integration tests.
Pristine sources come from git (HEAD, the 3.5.6 tag) when available, so an
already integrated working tree does not mask anchor problems. All
modifications and native executables stay in temporary directories.
"""

from contextlib import contextmanager
import gzip
import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


FIRMWARE = Path(__file__).resolve().parents[1]
DEFAULT_ELRS = FIRMWARE.parents[1] / "ExpressLRS-t3s3-lr1121" / "src"
ELRS = Path(os.environ.get("ELRS_SRC", DEFAULT_ELRS))


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, FIRMWARE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patches = load_module("wifi_config")
integration = load_module("integrate")


def apply(text, edits):
    for old, new in edits:
        if new in text:
            continue
        if text.count(old) != 1:
            raise AssertionError(f"Expected one anchor, found {text.count(old)}: {old[:90]!r}")
        text = text.replace(old, new, 1)
    return text


def pristine(relative):
    result = subprocess.run(
        ["git", "-C", str(ELRS.parent), "show", f"HEAD:{ELRS.name}/{relative}"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode == 0:
        return result.stdout
    text = (ELRS / relative).read_text(encoding="utf-8")
    for old, new in integration.UPGRADES.get(relative, ()):
        text = text.replace(old, new)
    return text


def patched(relative):
    """``relative`` with every sniffer patch set applied (web UI and radio hooks)."""
    return apply(pristine(relative), integration.combined_edits()[relative])


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def function_body(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    for position in range(opening + 1, len(source)):
        depth += (source[position] == "{") - (source[position] == "}")
        if not depth:
            return source[start:position + 1]
    raise AssertionError("Unclosed function")


def options_harness(radio, update):
    """C++ program exercising the generated options code for one radio type."""
    band_checks = radio == "RADIO_LR1121"
    bad_band = "".join(
        '  request("{\\"flash-discriminator\\":42,\\"sniffer-band\\":%s}", %d);\n'
        % (bad, 400 if band_checks else 200)
        for bad in ("-1", "2", "256", "true", "\\\"1\\\"", "null", "1.5")
    )
    # Every sniffer keeps the band key as the new-format marker, so the saved
    # band (1, from the previous request) survives a request that omits it.
    retained_band = "assert(firmwareOptions.ghost_subghz);"
    return r'''
#include <ArduinoJson.h>
#include <cassert>
#include <cstdint>
#include <string>
#define GHOST_SNIFFER 1
#define ''' + radio + r''' 1
struct Options { bool ghost_subghz; uint8_t domain; int32_t wifi_auto_on_interval; uint32_t flash_discriminator = 42; } firmwareOptions;
int writes = 0;
std::string stored;
struct File {
  size_t write(uint8_t c) { stored.push_back(c); return 1; }
  size_t write(const uint8_t *p, size_t n) { stored.append((const char*)p, n); return n; }
};
struct Storage { File open(const char*, const char*) { ++writes; stored.clear(); return File(); } } SPIFFS;
struct AsyncWebServerRequest { int status = 0; void send(int code, const char* = nullptr, const char* = nullptr) { status = code; } };
void load(const char *text) {
  JsonDocument doc;
  assert(!deserializeJson(doc, text));
''' + patches.LOAD_WIFI + "\n" + patches.LOAD_BAND + r'''
}
std::string save() {
  JsonDocument doc;
''' + patches.SAVE_WIFI + "\n" + patches.SAVE_BAND + r'''
  doc["domain"] = firmwareOptions.domain;
  std::string out;
  serializeJson(doc, out);
  return out;
}
''' + patches.VALIDATE_SETTINGS + update + r'''
void request(const std::string &text, int expected) {
  JsonDocument doc;
  assert(!deserializeJson(doc, text));
  JsonVariant json = doc.as<JsonVariant>();
  AsyncWebServerRequest req;
  writes = 0;
  UpdateSettings(&req, json);
  assert(req.status == expected);
  assert(writes == (expected == 200 ? 1 : 0));
}
int main() {
  load("{}");
  assert(!firmwareOptions.ghost_subghz && firmwareOptions.domain == 2 && firmwareOptions.wifi_auto_on_interval == 60000);
  load("{\"domain\":2,\"wifi-on-interval\":-1}");
  assert(firmwareOptions.wifi_auto_on_interval == 60000);
  load("{\"sniffer-band\":0,\"wifi-on-interval\":-1}");
  assert(!firmwareOptions.ghost_subghz && firmwareOptions.wifi_auto_on_interval == -1);
  auto disabled = save(); load(disabled.c_str());
  assert(firmwareOptions.wifi_auto_on_interval == -1);
  for (int domain = 0; domain < 4; ++domain) {
    auto text = std::string("{\"sniffer-band\":1,\"domain\":") + std::to_string(domain) + ",\"wifi-on-interval\":60}";
    load(text.c_str());
    assert(firmwareOptions.ghost_subghz && firmwareOptions.domain == domain);
    auto saved = save(); load(saved.c_str());
    assert(firmwareOptions.ghost_subghz && firmwareOptions.domain == domain && firmwareOptions.wifi_auto_on_interval == 60000);
  }
  load("{\"sniffer-band\":\"1\",\"domain\":256,\"wifi-on-interval\":2147484}");
  assert(!firmwareOptions.ghost_subghz && firmwareOptions.domain == 2 && firmwareOptions.wifi_auto_on_interval == 60000);
  load("{\"sniffer-band\":1,\"domain\":-1,\"wifi-on-interval\":0}");
  assert(firmwareOptions.ghost_subghz && firmwareOptions.domain == 2 && firmwareOptions.wifi_auto_on_interval == 0);
  request("{\"flash-discriminator\":41,\"sniffer-band\":1,\"domain\":2}", 409);
''' + bad_band + r'''
  for (const char *bad : {"-1", "4", "256", "true", "\"2\"", "null", "1.5"}) {
    auto text = std::string("{\"flash-discriminator\":42,\"domain\":") + bad + "}";
    request(text, 400);
  }
  request("{\"flash-discriminator\":42,\"wifi-on-interval\":2147484}", 400);
  request("{\"flash-discriminator\":42,\"sniffer-band\":1,\"domain\":3,\"wifi-on-interval\":-1}", 200);
  load(stored.c_str());
  assert(firmwareOptions.ghost_subghz && firmwareOptions.domain == 3 && firmwareOptions.wifi_auto_on_interval == -1);
  request("{\"flash-discriminator\":42}", 200);
  load(stored.c_str());
  ''' + retained_band + r'''
  assert(firmwareOptions.domain == 3 && firmwareOptions.wifi_auto_on_interval == -1);
}
'''


@unittest.skipUnless((ELRS / "lib/OPTIONS/options.cpp").exists(), "Set ELRS_SRC to an ELRS 3.5.6 src checkout")
class WifiConfigTests(unittest.TestCase):
    def test_all_anchors_apply_and_are_idempotent(self):
        for relative, edits in patches.EXTRA_EDITS.items():
            with self.subTest(file=relative):
                result = patched(relative)
                self.assertEqual(apply(result, edits), result)

    def test_native_options_and_http_validation(self):
        compiler = shutil.which("g++")
        headers = next(ELRS.glob(".pio/libdeps/*/ArduinoJson/src/ArduinoJson.h"), None)
        if not compiler or not headers:
            self.skipTest("g++ and the checkout's ArduinoJson dependency are required")
        update = function_body(patched("lib/WIFI/devWIFI.cpp"), "static void UpdateSettings(")
        for radio in ("RADIO_LR1121", "RADIO_SX126X"):
            with self.subTest(radio=radio), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "options_test.cpp"
                executable = Path(temporary) / "options_test.exe"
                source.write_text(options_harness(radio, update), encoding="utf-8")
                subprocess.run([compiler, "-std=c++17", "-I", str(headers.parent), str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
                subprocess.run([str(executable)], check=True, capture_output=True, text=True)

    def test_rendered_ui_and_browser_band_behavior(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the browser behavior check")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            shutil.copytree(ELRS / "html", directory / "html")
            for relative in ("html/index.html", "html/scan.js"):
                (directory / relative).write_text(patched(relative), encoding="utf-8")
            build_source = patched("python/build_html.py").split("target_name = env['PIOENV'].upper()", 1)[0]
            namespace = {"Import": lambda name: None}
            sys.path.insert(0, str(ELRS / "python"))
            try:
                # ELRS's external/__init__.py imports its flashing CLI. The
                # renderer only needs the vendored template/minifier modules.
                external = types.ModuleType("external")
                external.__path__ = [str(ELRS / "python/external")]
                with patch.dict(sys.modules, {"external": external}):
                    exec(build_source, namespace)
            finally:
                sys.path.pop(0)

            def render(file, flags, is_tx=False):
                env = {"GIT_VERSION": "test", "GIT_SHA": "test", "PIOENV": "T3S3_RX_Test", "BUILD_FLAGS": flags}
                output = io.StringIO()
                with working_directory(directory):
                    namespace["build_html"](file, "CONTENT", output, env, is_tx)
                blob = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-f]{2})", output.getvalue()))
                return gzip.decompress(blob).decode()

            flags = ["-DRADIO_LR1121=1", "-DGHOST_SNIFFER=1"]
            html = render("index.html", flags)
            self.assertIn('id=sniffer-band', html)
            self.assertIn("Sub-1 GHz", html)
            self.assertIn("FCC915", html)
            self.assertNotIn("AU433", html)
            self.assertIn('id=phrase', html)
            self.assertIn('id=uid', html)
            self.assertIn("AU433", render("index.html", ["-DRADIO_LR1121=1"]))
            self.assertNotIn('id=sniffer-band', render("index.html", ["-DRADIO_LR1121=1"]))
            self.assertNotIn('id=sniffer-band', render("index.html", flags, is_tx=True))
            self.assertIn('id=sniffer-band', render("index.html", ["-DRADIO_LR1121=1", "-D", "GHOST_SNIFFER=1"]))

            # The SX1280 sniffer has no sub-GHz plan and no band switch.
            html2400 = render("index.html", ["-DRADIO_SX128X=1", "-DGHOST_SNIFFER=1"])
            self.assertNotIn('id=sniffer-band', html2400)
            self.assertNotIn("FCC915", html2400)

            # The SX1262 sniffer offers the 863-928 MHz plans but no band switch.
            flags126 = ["-DRADIO_SX126X=1", "-DGHOST_SNIFFER=1"]
            html126 = render("index.html", flags126)
            self.assertNotIn('id=sniffer-band', html126)
            self.assertNotIn("Sub-1 GHz", html126)
            self.assertIn("FCC915", html126)
            self.assertIn("Frequency plan", html126)
            self.assertNotIn("AU433", html126)
            self.assertIn("AU433", render("index.html", ["-DRADIO_SX126X=1"]))

            script = render("scan.js", flags)
            (directory / "scan.js").write_text(script, encoding="utf-8")
            subprocess.run([node, "--check", str(directory / "scan.js")], check=True, capture_output=True, text=True)
            # Run the actual rendered functions with a minimal DOM and HTTP sink.
            functions = "\n".join(function_body(script, "function " + name + "(") for name in ("updateSnifferBand", "updateOptions", "submitOptions"))
            browser_dom = r'''
const assert = require('assert');
const fields = new Map();
function _(key) {
  if (!fields.has(key)) {
    let value = '';
    fields.set(key, {get value(){return value;}, set value(v){value=String(v);}, style:{}, type:'text', classList:{contains:()=>false}});
  }
  return fields.get(key);
}
let sent;
class XMLHttpRequest { open() {} setRequestHeader() {} send(body) { sent=JSON.parse(body); } }
class FormData { constructor() { return [['sniffer-band',_('sniffer-band').value],['domain',_('domain').value],['wifi-on-interval',_('wifi-on-interval').value]]; } }
_('upload_options').querySelectorAll = () => [];
'''
            browser_test = browser_dom + functions + r'''
updateOptions({'sniffer-band':0,domain:2,'wifi-on-interval':60});
assert.equal(_('sniffer-band').value,0);
assert.equal(_('sniffer-domain').style.display,'none');
updateOptions({'sniffer-band':1,domain:3,'wifi-on-interval':60});
assert.equal(_('sniffer-domain').style.display,'block');
assert.equal(_('domain').value,'3');
_('sniffer-band').value='1'; updateSnifferBand();
assert.equal(_('sniffer-domain').style.display,'block');
_('domain').value='1'; _('wifi-on-interval').value='';
submitOptions({stopPropagation(){},preventDefault(){}});
assert.equal(sent['sniffer-band'],1); assert.equal(sent.domain,1); assert.equal(sent['wifi-on-interval'],-1);
updateOptions({'sniffer-band':0,domain:2,'wifi-on-interval':-1});
assert.equal(_('wifi-on-interval').value,'');
assert.equal(_('sniffer-domain').style.display,'none');
'''
            (directory / "browser_test.js").write_text(browser_test, encoding="utf-8")
            subprocess.run([node, str(directory / "browser_test.js")], check=True, capture_output=True, text=True)

            # SX1262: same sniffer defaults, no band element is ever touched.
            script126 = render("scan.js", flags126)
            (directory / "scan126.js").write_text(script126, encoding="utf-8")
            subprocess.run([node, "--check", str(directory / "scan126.js")], check=True, capture_output=True, text=True)
            self.assertNotIn("updateSnifferBand", script126)
            self.assertNotIn("sniffer-band", script126)
            functions126 = "\n".join(function_body(script126, "function " + name + "(") for name in ("updateOptions", "submitOptions"))
            browser_test126 = browser_dom + functions126 + r'''
updateOptions({domain:1,'wifi-on-interval':60});
assert.equal(_('domain').value,'1');
assert.equal(_('wifi-on-interval').value,'60');
updateOptions({'wifi-on-interval':-1});
assert.equal(_('domain').value,'2');
assert.equal(_('wifi-on-interval').value,'');
_('domain').value='0';
submitOptions({stopPropagation(){},preventDefault(){}});
assert.equal(sent.domain,0); assert.equal(sent['wifi-on-interval'],-1);
'''
            (directory / "browser_test126.js").write_text(browser_test126, encoding="utf-8")
            subprocess.run([node, str(directory / "browser_test126.js")], check=True, capture_output=True, text=True)

            # SX1280: sniffer defaults without any sub-GHz or band references.
            script2400 = render("scan.js", ["-DRADIO_SX128X=1", "-DGHOST_SNIFFER=1"])
            (directory / "scan2400.js").write_text(script2400, encoding="utf-8")
            subprocess.run([node, "--check", str(directory / "scan2400.js")], check=True, capture_output=True, text=True)
            self.assertNotIn("sniffer-band", script2400)
            self.assertNotIn("_('domain').value='2'", script2400)
            self.assertIn("_('wifi-on-interval').value=''", function_body(script2400, "function updateOptions("))


if __name__ == "__main__":
    unittest.main()
