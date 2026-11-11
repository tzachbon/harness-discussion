"""Windows microphone usage parsing and registry access."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from harness_discussion.win import mic_use


class InUseTest(unittest.TestCase):
    def test_fixture_and_full_path_exclusion(self):
        entries = json.loads((Path(__file__).parent / "fixtures/meetings/registry.json").read_text())
        entries["nonpackaged"][r"D:#Other#python.exe"] = {"LastUsedTimeStop": 0}
        self.assertEqual(mic_use._in_use(entries, {r"c:\PYTHON\PYTHON.EXE"}),
                         {"msteams_8wekyb3d8bbwe", "zoom.exe", "python.exe"})
        self.assertEqual(mic_use._in_use(entries, {r"c:\python\python.exe", r"d:\other\python.exe"}),
                         {"msteams_8wekyb3d8bbwe", "zoom.exe"})


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class RegistryTest(unittest.TestCase):
    def test_users_reads_registry_and_defaults_to_both_interpreters(self):
        import winreg

        class Key:
            def __init__(self, name):
                self.name = name

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        tree = {"root": ["NonPackaged", "App_ID", "Missing"],
                "NonPackaged": [r"C:#Venv#python.exe", r"C:#Base#python.exe", r"D:#Other#python.exe"]}

        def open_key(parent, name):
            return Key("root" if parent == winreg.HKEY_CURRENT_USER else name)

        def query_value(key, name):
            if key.name == "Missing":
                raise FileNotFoundError
            return 0, winreg.REG_QWORD

        with patch.object(winreg, "OpenKey", side_effect=open_key), \
                patch.object(winreg, "QueryInfoKey", side_effect=lambda key: (len(tree[key.name]), 0, 0)), \
                patch.object(winreg, "EnumKey", side_effect=lambda key, i: tree[key.name][i]), \
                patch.object(winreg, "QueryValueEx", side_effect=query_value), \
                patch.object(sys, "executable", r"C:\Venv\python.exe"), \
                patch.object(sys, "_base_executable", r"C:\Base\python.exe", create=True):
            self.assertEqual(mic_use.users(), {"app_id", "python.exe"})
