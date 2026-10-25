"""Publish the tested installers without moving tags or replacing completed assets.

Usage: publish_release.py ARTIFACT... (env: RELEASE_VERSION, RELEASE_COMMIT, GITHUB_REPOSITORY)
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from release_version import parse_tag


# ponytail: says unsigned until SignPath signing starts, then update this line.
POLICY = ("Code signing policy: https://github.com/tzachbon/harness-discussion#code-signing-policy. "
          "Signing is pending, this installer is unsigned.")

def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def remote_tags():
    refs = {ref: sha for sha, ref in (line.split() for line in
                                    command("git", "ls-remote", "--tags", "origin").splitlines())}
    return {ref.removeprefix("refs/tags/"): refs.get(ref + "^{}", sha)
            for ref, sha in refs.items() if not ref.endswith("^{}")}


def read_release(repository, tag):
    return json.loads(command("gh", "release", "view", tag, "--repo", repository,
                              "--json", "assets,body,isDraft"))


def complete_asset(release, name):
    asset = next((asset for asset in release["assets"] if asset["name"] == name), None)
    if asset and (asset["state"] != "uploaded" or asset["size"] <= 0):
        raise ValueError(f"Incomplete {name} asset. Repair the failed upload before rerunning; no asset was overwritten.")
    return asset is not None


def publish(version, commit, repository, artifacts):
    tag = f"v{version}"
    if parse_tag(tag) is None or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid release version or commit SHA")
    artifacts = [Path(artifact) for artifact in artifacts]
    if not artifacts:
        raise ValueError("No installers to publish")
    for artifact in artifacts:
        if not artifact.is_file() or not artifact.stat().st_size:
            raise ValueError(f"The tested installer {artifact.name} is missing or empty")
    tags = remote_tags()
    if tag not in tags:
        command("gh", "api", "--method", "POST", f"repos/{repository}/git/refs",
                "-f", f"ref=refs/tags/{tag}", "-f", f"sha={commit}")
        tags = remote_tags()
    if tags.get(tag) != commit:
        raise ValueError(f"{tag} does not point to the tested commit {commit}")
    # A failed read may be auth/network related; create fails rather than modifying an existing release.
    existing = subprocess.run(["gh", "release", "view", tag, "--repo", repository,
                               "--json", "assets,body,isDraft"], capture_output=True, text=True,
                              encoding="utf-8")
    if existing.returncode:
        command("gh", "release", "create", tag, str(artifacts[0]), "--repo", repository,
                "--verify-tag", "--draft", "--title", f"Harness Discussion {tag}", "--generate-notes",
                "--notes", POLICY)  # gh puts it above the generated notes
        release = read_release(repository, tag)
    else:
        release = json.loads(existing.stdout)
    for artifact in artifacts:  # the release stays a draft until every installer is complete
        if not complete_asset(release, artifact.name):
            command("gh", "release", "upload", tag, str(artifact), "--repo", repository)
            release = read_release(repository, tag)
            if not complete_asset(release, artifact.name):
                raise ValueError(f"{artifact.name} upload did not complete; release remains unpublished")
    if release["isDraft"]:
        published = command("gh", "api", "--paginate", f"repos/{repository}/releases", "--jq",
                            ".[] | select(.draft == false and .prerelease == false) | .tag_name")
        versions = [value for name in published.splitlines() if (value := parse_tag(name)) is not None]
        latest = "true" if not versions or parse_tag(tag) >= max(versions) else "false"
        notes = [] if POLICY in (release.get("body") or "") else [  # a draft resumed from an older run
            "--notes", f"{POLICY}\n\n{release.get('body') or ''}".rstrip()]
        command("gh", "release", "edit", tag, "--repo", repository, "--draft=false", f"--latest={latest}", *notes)
        if read_release(repository, tag)["isDraft"]:
            raise ValueError("Release is still a draft after publication")
    print(f"Published {tag} at {commit}")


if __name__ == "__main__":
    publish(os.environ["RELEASE_VERSION"], os.environ["RELEASE_COMMIT"],
            os.environ["GITHUB_REPOSITORY"], sys.argv[1:])
