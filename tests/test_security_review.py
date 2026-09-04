from __future__ import annotations

import subprocess
from pathlib import Path


def test_git_does_not_track_env_secret_files() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        ["git", "ls-files", ".env", ".env.*"],
        cwd=repo_root,
        capture_output=True,
        check=True,
        text=True,
    )

    tracked_files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert tracked_files == [".env.example"]
