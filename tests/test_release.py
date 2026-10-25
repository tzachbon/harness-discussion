"""Release history uses real Git. Publication tests isolate GitHub command boundaries."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness_discussion import updates

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
import publish_release
import release_version


class SemVer(unittest.TestCase):
    def test_stable_tags_only(self):
        self.assertEqual(release_version.parse_tag("v12.3.4"), (12, 3, 4))
        for tag in ("v01.0.0", "v1.2", "v1.2.3-rc.1", "v1.2.3+build", "1.2.3", "v1٢.0.0"):
            self.assertIsNone(release_version.parse_tag(tag), tag)

    def test_strongest_commit_bump_and_reset(self):
        cases = ((["docs: describe use", "fix(ui): repair close"], (1, 2, 4)),
                 (["feat: add control", "fix: repair"], (1, 3, 0)),
                 (["feat(ui): add control"], (1, 3, 0)),
                 (["refactor(ui)!: remove old contract"], (2, 0, 0)),
                 (["fix: change\n\nBREAKING CHANGE: old interface removed"], (2, 0, 0)),
                 (["fix: change\n\nBREAKING-CHANGE: old interface removed"], (2, 0, 0)),
                 (["fix: small\n\nExample:\nfeat: quoted text"], (1, 2, 4)),
                 (["feat: add", "chore!: break", "fix: repair"], (2, 0, 0)))
        for messages, expected in cases:
            self.assertEqual(release_version.bump((1, 2, 3), messages), expected)


class ReleaseHistory(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "-b", "master")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "release-test@example.invalid")
        self.commit("Initial source")
        self.git("tag", "v0.1.0")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True,
                              text=True, encoding="utf-8", check=True).stdout.strip()

    def commit(self, message):
        self.git("commit", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def version(self, ref="refs/heads/master", check=True):
        env = {**os.environ, "GITHUB_REF": ref}
        env.pop("GITHUB_OUTPUT", None)
        return subprocess.run([sys.executable, str(Path(release_version.__file__).resolve())],
                              cwd=self.repo, env=env, capture_output=True, text=True, check=check)

    def test_order_and_intermediate_tags_do_not_change_versions(self):
        first = self.commit("feat: first control")
        second = self.commit("feat: second control")
        self.assertEqual(self.version().stdout.strip(), "0.3.0")
        self.git("checkout", "--detach", first)
        self.assertEqual(self.version().stdout.strip(), "0.2.0")
        self.git("tag", "v0.2.0")
        self.assertEqual(self.version().stdout.strip(), "0.2.0")
        self.git("checkout", "--detach", second)
        self.assertEqual(self.version().stdout.strip(), "0.3.0")
        self.git("tag", "v0.3.0")
        self.git("checkout", "--detach", first)
        self.assertEqual(self.version().stdout.strip(), "0.2.0")

    def test_normal_merge_reads_new_branch_commits_but_ignores_side_branch_tags(self):
        self.git("switch", "-c", "feature")
        self.commit("feat: add voice")
        self.git("tag", "v9.0.0")
        self.git("switch", "master")
        self.git("merge", "--no-ff", "feature", "-m", "Merge pull request #10")
        self.assertEqual(self.version().stdout.strip(), "0.2.0")
        self.commit("docs: describe voice")
        self.assertEqual(self.version().stdout.strip(), "0.2.1")

    def test_breaking_merge_and_annotated_tag_rerun(self):
        self.git("switch", "-c", "feature")
        self.commit("refactor!: remove old voice")
        self.git("switch", "master")
        self.git("merge", "--no-ff", "feature", "-m", "Merge feature")
        self.assertEqual(self.version().stdout.strip(), "1.0.0")
        self.git("tag", "-a", "v1.0.0", "-m", "Release")
        self.assertEqual(self.version().stdout.strip(), "1.0.0")
        self.assertEqual(self.version("refs/tags/v1.0.0").stdout.strip(), "1.0.0")

    def test_squash_merge_and_manual_tag_validation(self):
        self.commit("feat(captions): add captions (#10)")
        self.assertEqual(self.version().stdout.strip(), "0.2.0")
        self.git("tag", "v1.2.3")
        self.assertEqual(self.version("refs/tags/v1.2.3").stdout.strip(), "1.2.3")
        self.commit("fix: adjust captions")
        self.assertNotEqual(self.version("refs/tags/v1.2.3", check=False).returncode, 0)
        self.git("tag", "v1.3.0-rc.1")
        self.assertNotEqual(self.version("refs/tags/v1.3.0-rc.1", check=False).returncode, 0)

    def test_missing_stable_anchor_fails(self):
        self.git("tag", "-d", "v0.1.0")
        result = self.version(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No stable", result.stderr)


class Publication(unittest.TestCase):
    commit = "a" * 40

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifact = Path(directory.name) / "HarnessDiscussion-Setup.exe"
        self.artifact.write_bytes(b"Test installer")
        self.dmg = Path(directory.name) / "HarnessDiscussion.dmg"
        self.dmg.write_bytes(b"Test disk image")
        self.artifacts = [self.artifact]
        self.tags, self.release, self.published, self.calls = {}, None, [], []
        self.upload_state, self.upload_states = "uploaded", {}
        boundary = patch.object(subprocess, "run", side_effect=self.run_command)
        boundary.start()
        self.addCleanup(boundary.stop)

    def asset(self, state="uploaded", path=None):
        path = path or self.artifact
        return {"name": path.name, "state": state,
                "size": path.stat().st_size if state == "uploaded" else 0,
                "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "browser_download_url": f"https://github.com/tzachbon/harness-discussion/releases/download/v0.2.0/{path.name}"}

    def run_command(self, args, **kwargs):
        args = tuple(args)
        self.calls.append(args)
        status, output = 0, ""
        if args[:3] == ("git", "ls-remote", "--tags"):
            output = "\n".join(f"{sha}\trefs/tags/{tag}" for tag, sha in self.tags.items())
        elif args[:4] == ("gh", "api", "--method", "POST"):
            fields = dict(field.split("=", 1) for field in args if field.startswith(("ref=", "sha=")))
            self.tags[fields["ref"].removeprefix("refs/tags/")] = fields["sha"]
        elif args[:3] == ("gh", "release", "view"):
            status, output = (1, "") if self.release is None else (0, json.dumps(self.release))
        elif args[:3] == ("gh", "release", "create"):
            self.assertIn("--verify-tag", args)
            self.assertNotIn("--target", args)
            self.assertIn("--generate-notes", args)
            self.assertTrue(args[args.index("--notes") + 1].startswith(  # SignPath requires it on release pages
                "Code signing policy: https://github.com/tzachbon/harness-discussion#code-signing-policy"))
            self.release = {"isDraft": True, "body": args[args.index("--notes") + 1],
                            "assets": [self.asset(self.upload_state, Path(args[4]))]}
        elif args[:3] == ("gh", "release", "upload"):
            self.assertNotIn("--clobber", args)
            path = Path(args[4])
            self.release["assets"].append(self.asset(self.upload_states.get(path.name, self.upload_state), path))
        elif args[:3] == ("gh", "api", "--paginate"):
            output = "\n".join(self.published)
        elif args[:3] == ("gh", "release", "edit"):
            self.release["isDraft"] = False
            if "--notes" in args:
                self.release["body"] = args[args.index("--notes") + 1]
        else:
            self.fail(f"Unexpected external command: {args}")
        result = subprocess.CompletedProcess(args, status, stdout=output, stderr="")
        if kwargs.get("check") and status:
            raise subprocess.CalledProcessError(status, args, output=output)
        return result

    def publish(self):
        publish_release.publish("0.2.0", self.commit, "owner/repo", self.artifacts)

    def test_new_release_is_bound_to_tested_commit_and_published(self):
        self.publish()
        self.assertEqual(sum("--notes" in call for call in self.calls), 1)  # the policy is not added twice
        self.assertEqual(self.tags["v0.2.0"], self.commit)
        self.assertFalse(self.release["isDraft"])
        self.assertTrue(any("--latest=true" in call for call in self.calls))

    def test_published_installer_metadata_is_accepted_by_updater(self):
        self.publish()
        metadata = {"tag_name": "v0.2.0", "draft": self.release["isDraft"],
                    "prerelease": False, "assets": self.release["assets"]}
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.geturl.return_value = updates.API
        reply.read.return_value = json.dumps(metadata).encode()
        with patch.object(updates, "urlopen", return_value=reply), \
                patch.object(updates, "ASSET", self.artifact.name), \
                patch.object(updates, "version", return_value="0.1.1"):
            self.assertEqual(updates.check()["version"], "0.2.0")

    def test_both_installers_are_published_and_each_platform_finds_its_own(self):
        self.artifacts = [self.artifact, self.dmg]
        self.publish()
        self.assertFalse(self.release["isDraft"])
        self.assertEqual([a["name"] for a in self.release["assets"]], ["HarnessDiscussion-Setup.exe", "HarnessDiscussion.dmg"])
        metadata = {"tag_name": "v0.2.0", "draft": False, "prerelease": False, "assets": self.release["assets"]}
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.geturl.return_value = updates.API
        reply.read.return_value = json.dumps(metadata).encode()
        for name in ("HarnessDiscussion-Setup.exe", "HarnessDiscussion.dmg"):
            with self.subTest(name=name), patch.object(updates, "urlopen", return_value=reply), \
                    patch.object(updates, "ASSET", name), patch.object(updates, "version", return_value="0.1.1"):
                self.assertEqual(updates.check()["name"], name)

    def test_failed_second_upload_keeps_the_release_a_draft(self):
        self.artifacts = [self.artifact, self.dmg]
        self.upload_states = {"HarnessDiscussion.dmg": "starter"}
        with self.assertRaisesRegex(ValueError, "HarnessDiscussion.dmg"):
            self.publish()
        self.assertTrue(self.release["isDraft"])
        self.assertFalse(any(call[:3] == ("gh", "release", "edit") for call in self.calls))

    def test_any_missing_installer_fails_before_writes(self):
        self.artifacts = [self.artifact, self.dmg]
        self.dmg.unlink()
        with self.assertRaisesRegex(ValueError, "HarnessDiscussion.dmg"):
            self.publish()
        self.assertEqual(self.calls, [])

    def test_annotated_tag_is_peeled_and_completed_release_is_a_noop(self):
        self.tags = {"v0.2.0": "b" * 40, "v0.2.0^{}": self.commit}
        self.release = {"isDraft": False, "assets": [self.asset()]}
        self.publish()
        self.assertEqual([call[:3] for call in self.calls],
                         [("git", "ls-remote", "--tags"), ("gh", "release", "view")])

    def test_existing_tag_on_different_commit_fails_before_writes(self):
        self.tags = {"v0.2.0": "b" * 40}
        with self.assertRaisesRegex(ValueError, "tested commit"):
            self.publish()
        self.assertEqual(len(self.calls), 1)

    def test_interrupted_draft_completes_before_or_after_upload(self):
        for assets in ([], [self.asset()]):
            with self.subTest(assets=assets):
                self.tags = {"v0.2.0": self.commit}
                self.release = {"isDraft": True, "assets": assets.copy()}
                self.publish()
                self.assertFalse(self.release["isDraft"])
                self.assertEqual(len(self.release["assets"]), 1)
                self.assertTrue(self.release["body"].startswith("Code signing policy: "))  # added on resume

    def test_delayed_older_version_does_not_replace_latest(self):
        self.published = ["v0.3.0", "v0.1.0", "v9.0.0-rc.1", "legacy"]
        self.publish()
        self.assertTrue(any("--latest=false" in call for call in self.calls))

    def test_unpublished_higher_tag_does_not_suppress_latest(self):
        self.tags = {"v9.0.0": "b" * 40}
        self.published = ["v0.1.0"]
        self.publish()
        self.assertTrue(any("--latest=true" in call for call in self.calls))

    def test_incomplete_existing_or_new_asset_never_publishes(self):
        for existing in (True, False):
            with self.subTest(existing=existing):
                self.tags = {"v0.2.0": self.commit}
                self.release = {"isDraft": True, "assets": [self.asset("starter")]} if existing else None
                self.upload_state, self.calls = "starter", []
                with self.assertRaisesRegex(ValueError, "Incomplete"):
                    self.publish()
                self.assertTrue(self.release["isDraft"])
                self.assertFalse(any(call[:3] == ("gh", "release", "edit") for call in self.calls))

    def test_missing_installer_and_invalid_inputs_do_not_write(self):
        for version, commit in (("0.2.0-rc.1", self.commit), ("0.2.0", "not-a-sha")):
            with self.assertRaises(ValueError):
                publish_release.publish(version, commit, "owner/repo", [self.artifact])
        self.artifact.unlink()
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
