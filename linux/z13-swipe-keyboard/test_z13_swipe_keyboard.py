"""Hermetic unit tests for the swipe-up keyboard KWin script: its metadata.json and main.js, and install.sh run in dry-run mode or against fake KDE tools in a temporary home; nothing touches KWin, the session bus, the real config or the real home directory."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

PKG_DIR = os.path.dirname(os.path.realpath(__file__))
METADATA = os.path.join(PKG_DIR, "metadata.json")
MAIN_JS = os.path.join(PKG_DIR, "contents", "code", "main.js")
INSTALLER = os.path.join(PKG_DIR, "install.sh")
PKG_ID = "z13swipekeyboard"
ENABLE_KEY = PKG_ID + "Enabled"
FAKE_TOOLS = ("kpackagetool6", "kwriteconfig6", "qdbus6")
# A fake KDE tool: it records its name and arguments in the log file named by Z13_FAKE_LOG.
FAKE_TOOL_SCRIPT = '#!/bin/sh\necho "$(basename "$0") $*" >> "$Z13_FAKE_LOG"\n'


class MetadataTest(unittest.TestCase):
    def setUp(self):
        with open(METADATA, encoding="utf-8") as handle:
            self.meta = json.load(handle)

    def test_is_a_kwin_script_package(self):
        self.assertEqual(self.meta["KPackageStructure"], "KWin/Script")
        self.assertEqual(self.meta["X-Plasma-API"], "javascript")

    def test_plugin_has_expected_id_and_required_keys(self):
        plugin = self.meta["KPlugin"]
        for key in ("Id", "Name", "Description", "Version", "License"):
            self.assertTrue(plugin.get(key), f"missing KPlugin.{key}")
        self.assertEqual(plugin["Id"], PKG_ID)
        self.assertRegex(plugin["Id"], r"^[A-Za-z0-9]+$")
        self.assertEqual(plugin["Name"], "Swipe up for keyboard")
        self.assertEqual(plugin["Version"], "1.0")
        self.assertEqual(plugin["License"], "GPL-2.0-or-later")


class MainJsTest(unittest.TestCase):
    def setUp(self):
        with open(MAIN_JS, encoding="utf-8") as handle:
            self.text = handle.read()

    def test_registers_the_bottom_touch_edge(self):
        self.assertIn("registerTouchScreenEdge(KWin.ElectricBottom", self.text)

    def test_calls_force_activate_on_the_virtual_keyboard(self):
        self.assertIn('callDBus("org.kde.KWin"', self.text)
        self.assertIn('"/VirtualKeyboard"', self.text)
        self.assertIn('"org.kde.kwin.VirtualKeyboard"', self.text)
        self.assertIn('"forceActivate"', self.text)

    def test_stays_short(self):
        self.assertLess(len(self.text.splitlines()), 25)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="z13swipe-test-")
        self.home = os.path.join(self.root, "home")
        self.fake_bin = os.path.join(self.root, "bin")
        self.log = os.path.join(self.root, "calls.log")
        os.makedirs(self.home)
        os.makedirs(self.fake_bin)
        for tool in FAKE_TOOLS:
            path = os.path.join(self.fake_bin, tool)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(FAKE_TOOL_SCRIPT)
            os.chmod(path, 0o755)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_installer(self, *args):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["PATH"] = self.fake_bin + os.pathsep + env.get("PATH", "")
        env["Z13_FAKE_LOG"] = self.log
        return subprocess.run(["bash", INSTALLER, *args], env=env, capture_output=True, text=True, timeout=60)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def test_bash_syntax_is_valid(self):
        result = subprocess.run(["bash", "-n", INSTALLER], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_prints_usage_and_exits_zero(self):
        result = self.run_installer("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: bash install.sh", result.stdout)

    def test_unknown_option_exits_two(self):
        result = self.run_installer("--bogus")
        self.assertEqual(result.returncode, 2)

    def test_dry_run_install_prints_commands_and_runs_nothing(self):
        result = self.run_installer("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"would run: kpackagetool6 --type=KWin/Script --install {PKG_DIR}", result.stdout)
        self.assertIn(f"would run: kwriteconfig6 --file kwinrc --group Plugins --key {ENABLE_KEY} true", result.stdout)
        self.assertIn("would run: qdbus6 org.kde.KWin /KWin reconfigure", result.stdout)
        self.assertIn("Dry run: nothing was changed.", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_dry_run_uninstall_prints_removal_and_runs_nothing(self):
        result = self.run_installer("--uninstall", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"would run: kwriteconfig6 --file kwinrc --group Plugins --key {ENABLE_KEY} false", result.stdout)
        self.assertIn("would run: qdbus6 org.kde.KWin /KWin reconfigure", result.stdout)
        self.assertIn(f"would run: kpackagetool6 --type=KWin/Script --remove {PKG_ID}", result.stdout)
        self.assertIn("Dry run: nothing was changed.", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_install_runs_the_three_steps_in_order(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            f"kpackagetool6 --type=KWin/Script --install {PKG_DIR}",
            f"kwriteconfig6 --file kwinrc --group Plugins --key {ENABLE_KEY} true",
            "qdbus6 org.kde.KWin /KWin reconfigure",
        ])

    def test_install_upgrades_when_already_installed(self):
        os.makedirs(os.path.join(self.home, ".local", "share", "kwin", "scripts", PKG_ID))
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0], f"kpackagetool6 --type=KWin/Script --upgrade {PKG_DIR}")

    def test_uninstall_runs_the_three_steps_in_order(self):
        result = self.run_installer("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            f"kwriteconfig6 --file kwinrc --group Plugins --key {ENABLE_KEY} false",
            "qdbus6 org.kde.KWin /KWin reconfigure",
            f"kpackagetool6 --type=KWin/Script --remove {PKG_ID}",
        ])

    def test_missing_kde_tools_warn_and_do_not_fail(self):
        bare = os.path.join(self.root, "bare")
        os.makedirs(bare)
        for tool in ("bash", "dirname", "id"):
            os.symlink(shutil.which(tool), os.path.join(bare, tool))
        env = {"HOME": self.home, "PATH": bare}
        result = subprocess.run([os.path.join(bare, "bash"), INSTALLER], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        for tool in FAKE_TOOLS:
            self.assertIn(f"warning: {tool} not found", result.stdout)
            self.assertIn(f"warning: {tool} is not installed, so this step was skipped", result.stdout)


if __name__ == "__main__":
    unittest.main()
