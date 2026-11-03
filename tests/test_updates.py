import io
import json
import os
from pathlib import Path
import tempfile
import sys
import shutil
import subprocess
import unittest
from unittest.mock import patch

from harness_discussion import updates


class Response(io.BytesIO):
    def geturl(self):
        return self.url


def response(data, url):
    result = Response(data)
    result.url = url
    return result


def release(tag="v0.10.0"):
    return {"tag_name": tag, "draft": False, "prerelease": False, "assets": [{
        "name": updates.ASSET, "state": "uploaded", "size": 3,
        "digest": "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "browser_download_url": f"https://github.com/tzachbon/harness-discussion/releases/download/{tag}/{updates.ASSET}"}]}


class Releases(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        cache = patch.object(updates, "CACHE", Path(directory.name))
        cache.start()
        self.addCleanup(cache.stop)
        updates._rate_limit_until = None
        self.addCleanup(setattr, updates, "_rate_limit_until", None)

    def test_only_a_newer_numeric_version_is_selected(self):
        for current, expected in (("0.9.0", True), ("0.10.0", False), ("1.0.0", False)):
            with patch.object(updates, "current_version", return_value=current), patch.object(
                    updates, "urlopen", return_value=response(json.dumps(release()).encode(), updates.API)):
                self.assertEqual(updates.check() is not None, expected)

    def test_feed_and_assets_use_the_renamed_repo(self):
        self.assertEqual(updates.API, "https://api.github.com/repos/tzachbon/harness-discussion/releases/latest")
        self.assertEqual(updates.ASSET, "HarnessDiscussion.dmg" if sys.platform == "darwin" else "HarnessDiscussion-Setup.exe")
        with patch.object(updates, "current_version", return_value="0.9.0"), patch.object(
                updates, "urlopen", return_value=response(json.dumps(release()).encode(), updates.API)):
            self.assertEqual(updates.check()["version"], "0.10.0")

    def test_invalid_release_cannot_be_selected(self):
        for field, value in (("tag_name", "v0.11.0-beta"), ("draft", True), ("prerelease", True),
                             ("assets", []), ("assets", None)):
            data = release()
            data[field] = value
            with self.subTest(field=field, value=value), patch.object(updates, "current_version", return_value="0.9.0"), patch.object(
                    updates, "urlopen", return_value=response(json.dumps(data).encode(), updates.API)):
                with self.assertRaises(ValueError):
                    updates.check()
        for field, value in (("browser_download_url", "https://evil.test/setup.exe"),
                             ("browser_download_url",  # the repo before the rename
                              f"https://github.com/tzachbon/codex-speak/releases/download/v0.10.0/{updates.ASSET}"),
                             ("digest", None), ("digest", "sha256:bad"), ("size", True),
                             ("size", 0), ("size", 2**40), ("state", "new")):
            data = release()
            data["assets"][0][field] = value
            with self.subTest(field=field), patch.object(updates, "current_version", return_value="0.9.0"), patch.object(
                    updates, "urlopen", return_value=response(json.dumps(data).encode(), updates.API)):
                with self.assertRaises(ValueError):
                    updates.check()


class Downloads(unittest.TestCase):
    def test_cleanup_failure_preserves_the_download_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(updates, "CACHE", Path(directory)), patch.object(
                updates, "urlopen", return_value=response(b"abd", "https://release-assets.githubusercontent.com/file")):
            with patch("shutil.os.unlink", side_effect=PermissionError("installer is locked")):
                with self.assertRaisesRegex(ValueError, "Installer verification failed"):
                    updates.download(release()["assets"][0])

    def test_bytes_are_verified_and_bad_downloads_are_removed(self):
        asset = {**release()["assets"][0], "version": "0.10.0"}
        for body, valid in ((b"abc", True), (b"abd", False), (b"ab", False), (b"abcd", False)):
            with tempfile.TemporaryDirectory() as directory, patch.object(updates, "CACHE", Path(directory)), patch.object(
                    updates, "urlopen", return_value=response(body, "https://release-assets.githubusercontent.com/file")):
                if valid:
                    path = updates.download(asset)
                    self.assertEqual(path.read_bytes(), b"abc")
                    self.assertTrue(path.is_relative_to(Path(directory)))
                else:
                    with self.assertRaises(ValueError):
                        updates.download(asset)
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_network_failure_and_untrusted_redirect_leave_no_installer(self):
        asset = release()["assets"][0]
        for url in ("http://github.com/file", "https://evil.test/file", "https://user@github.com/file"):
            with tempfile.TemporaryDirectory() as directory, patch.object(updates, "CACHE", Path(directory)), patch.object(
                    updates, "urlopen", return_value=response(b"abc", url)):
                with self.assertRaises(ValueError):
                    updates.download(asset)
                self.assertEqual(list(Path(directory).iterdir()), [])
            with self.assertRaises(ValueError):
                updates._HTTPSRedirect().redirect_request(None, None, 302, "", {}, url)
        with tempfile.TemporaryDirectory() as directory, patch.object(updates, "CACHE", Path(directory)), patch.object(
                updates, "urlopen", side_effect=TimeoutError("offline")):
            with self.assertRaises(TimeoutError):
                updates.download(asset)
            self.assertEqual(list(Path(directory).iterdir()), [])


@unittest.skipUnless(sys.platform == "win32", "the installer runs only on Windows")
class Installation(unittest.TestCase):
    def test_installer_uses_its_default_folder_without_a_shell_or_reboot(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(updates, "CACHE", Path(directory)), patch.object(
                sys, "frozen", True, create=True), patch.object(sys, "executable", r"C:\Apps\Custom\HarnessDiscussion.exe"), patch(
                "subprocess.Popen") as launch:
            path = Path(directory) / "update-123" / updates.ASSET
            path.parent.mkdir()
            path.write_bytes(b"abc")
            updates.install(path)
            args = launch.call_args.args[0]
            self.assertEqual(args, [str(path), "/SP-", "/SILENT", "/NORESTART", f"/LOG={path.parent / 'setup.log'}"])
            self.assertEqual(launch.call_args.kwargs["shell"], False)
        with patch.object(sys, "frozen", False, create=True), patch("subprocess.Popen") as launch:
            with self.assertRaises(ValueError):
                updates.install(Path("anything.exe"))
            launch.assert_not_called()


def _bundle(folder, version):
    app = Path(folder) / "Harness Discussion.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text(
        f'<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>test.hd</string>'
        f'<key>CFBundleShortVersionString</key><string>{version}</string>'
        f'<key>CFBundleExecutable</key><string>Harness Discussion</string></dict></plist>')
    exe = app / "Contents" / "MacOS" / "Harness Discussion"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True, capture_output=True)
    return app


@unittest.skipUnless(sys.platform == "darwin", "the macOS helper uses hdiutil and codesign")
class MacInstallation(unittest.TestCase):
    def run_helper(self, tamper=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = _bundle(root / "Applications", "1.0.0")
            source = _bundle(root / "dmg", "2.0.0")
            if tamper:
                (source / "Contents" / "MacOS" / "Harness Discussion").write_text("#!/bin/sh\necho changed\n")
            dmg = root / updates.ASSET
            subprocess.run(["hdiutil", "create", "-quiet", "-srcfolder", str(root / "dmg"), "-format", "UDZO",
                            str(dmg)], check=True)
            (root / "bin").mkdir()
            opener = root / "bin" / "open"
            opener.write_text(f'#!/bin/sh\necho "$1" > "{root}/opened"\n')
            opener.chmod(0o755)
            gone = subprocess.Popen(["true"])
            gone.wait()
            env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}"}
            subprocess.run(["/bin/sh", "-c", updates.MAC_INSTALL, "sh", str(gone.pid), str(dmg), str(installed)],
                           env=env, check=True, capture_output=True, timeout=60)
            version = subprocess.run(["defaults", "read", str(installed / "Contents" / "Info"),
                                      "CFBundleShortVersionString"], capture_output=True, text=True).stdout.strip()
            leftovers = sorted(p.name for p in installed.parent.iterdir())
            return version, (root / "opened").read_text().strip() == str(installed), leftovers

    def test_helper_swaps_the_app_and_reopens_it(self):
        self.assertEqual(self.run_helper(), ("2.0.0", True, ["Harness Discussion.app"]))

    def test_a_bad_signature_keeps_the_old_app_and_reopens_it(self):
        self.assertEqual(self.run_helper(tamper=True), ("1.0.0", True, ["Harness Discussion.app"]))

    def test_install_starts_the_helper_detached_for_the_running_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory).resolve() / "Harness Discussion.app"
            path = Path(directory) / "updates" / "update-123" / updates.ASSET
            path.parent.mkdir(parents=True)
            path.write_bytes(b"abc")
            with patch.object(updates, "CACHE", Path(directory) / "updates"), patch.object(
                    sys, "frozen", True, create=True), patch.object(
                    sys, "executable", str(app / "Contents" / "MacOS" / "Harness Discussion")), patch(
                    "subprocess.Popen") as launch:
                updates.install(path)
            args = launch.call_args.args[0]
            self.assertEqual(args, ["/bin/sh", "-c", updates.MAC_INSTALL, "sh", str(os.getpid()), str(path), str(app)])
            self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_copies_that_cannot_replace_themselves_are_refused(self):
        for executable in ("/private/var/folders/x/AppTranslocation/ID/d/Harness Discussion.app/Contents/MacOS/x",
                           "/usr/bin/python3",
                           "/System/Applications/Harness Discussion.app/Contents/MacOS/x"):
            with self.subTest(executable=executable), patch.object(sys, "executable", executable):
                with self.assertRaisesRegex(ValueError, "release page"):
                    updates.mac_target()


class Packaging(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is needed for the build script")
    def test_build_rejects_a_mismatched_tag_without_changing_workflow_permissions(self):
        script = Path(__file__).resolve().parents[1] / "packaging" / "build.ps1"
        command = r"""
        $ast = [Management.Automation.Language.Parser]::ParseFile($env:HARNESS_DISCUSSION_BUILD_SCRIPT, [ref]$null, [ref]$null)
        $guard = $ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.IfStatementAst] } | Select-Object -First 1
        if (-not $guard) { throw 'Release guard missing' }
        $version = '0.1.1'
        foreach ($pair in @(@('branch','master'), @('tag','v0.1.1'), @('tag','v0.1.2'))) {
            $env:GITHUB_REF_TYPE, $env:GITHUB_REF_NAME = $pair
            $rejected = $false
            try { & ([scriptblock]::Create($guard.Extent.Text)) } catch { $rejected = $true }
            if ($rejected -ne ($pair[1] -eq 'v0.1.2')) { throw "Unexpected guard result for $pair" }
        }
        """
        # Pass the path as data to avoid PowerShell interpolation.
        result = subprocess.run(["pwsh", "-NoProfile", "-Command", command], capture_output=True, text=True,
                                env={**os.environ, "HARNESS_DISCUSSION_BUILD_SCRIPT": str(script)})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
