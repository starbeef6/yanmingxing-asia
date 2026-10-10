#!/usr/bin/env python3
"""Direct Python launcher: no PowerShell file or execution-policy change.

The config holds approved file paths and commit attribution only. Navicat's
program/job and this directory's publisher are fixed, with no shell command.
Private-key contents and Navicat connection secrets are never read here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import time

from publish_windows import PublishError, publication_lock, validate_config_paths

NAVICAT_EXE = Path(r"G:\Navicat Premium 17.0.8 (x64)\Navicat Premium 17\navicat.exe")
NAVICAT_JOB = "ODSync20261010A"
PYTHON_EXE = Path(r"C:\Users\1\AppData\Local\Programs\Python\Python313\python.exe")
EXPORT_PATH = Path(r"C:\Users\1\Desktop\ODDaily20261010A.json")
ROOT = Path(__file__).resolve().parent
FIELDS = frozenset(("export_path", "git_path", "ssh_path", "key_file", "known_hosts_file",
                    "repo_dir", "snapshot_dir", "author_name", "author_email"))


class SyncError(RuntimeError):
    pass


def read_config(path: Path) -> dict[str, str]:
    if path.name != "sync-config.json" or path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise SyncError("Use the dedicated normal sync-config.json file.")
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(config, dict) or set(config) != FIELDS
            or any(not isinstance(value, str) or not value.strip()
                   or any(character in value for character in ("\0", "\r", "\n"))
                   for value in config.values())):
        raise SyncError("Only the approved paths and commit author are accepted.")
    return config


def validate_runtime(config: dict[str, str]) -> None:
    # No configurable shell, executable, Navicat job, publisher or CLI flags.
    expected = {
        "export_path": EXPORT_PATH,
        "git_path": ROOT / "PortableGit" / "cmd" / "git.exe",
        "ssh_path": ROOT / "PortableGit" / "usr" / "bin" / "ssh.exe",
        "key_file": ROOT / "keys" / "website_ed25519",
        "known_hosts_file": ROOT / "keys" / "github_known_hosts",
        "repo_dir": ROOT / "publish-repo",
        "snapshot_dir": ROOT / "snapshots",
    }
    if sys.platform != "win32" or sys.version_info < (3, 13) or Path(sys.executable).resolve() != PYTHON_EXE.resolve():
        raise SyncError("Use the verified Windows Python 3.13 executable.")
    for field, allowed in expected.items():
        if Path(config[field]).resolve() != allowed.resolve():
            raise SyncError("A configured path differs from the approved local layout.")
    for path in (NAVICAT_EXE, expected["git_path"], expected["ssh_path"],
                 expected["key_file"], expected["known_hosts_file"],
                 ROOT / "publish_windows.py", ROOT / "convert_snapshot.py"):
        if not path.is_file():
            raise SyncError("A configured runtime file is missing.")
    validate_config_paths(export=expected["export_path"], snapshots=expected["snapshot_dir"],
                          repo=expected["repo_dir"], private_key=expected["key_file"],
                          known_hosts=expected["known_hosts_file"])


def run_sync(config: dict[str, str]) -> None:
    repo = Path(config["repo_dir"]).resolve()
    # A separate live kernel lock covers query launch and child publishing;
    # the publisher retains its own distinct live lock. Crash releases both.
    with publication_lock(repo.with_name(repo.name + ".run")):
        started_at = datetime.now(timezone.utc)
        # SQL timestamps may be truncated to seconds. Ensure a genuinely new
        # query timestamp can be strictly later than the recorded start.
        time.sleep(1.1)
        process = subprocess.Popen(
            [str(NAVICAT_EXE), "-batchjob", NAVICAT_JOB],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False,
        )
        immediate_status = process.poll()
        if immediate_status is not None and immediate_status != 0:
            raise SyncError("The Navicat launcher exited unsuccessfully.")
        # Do not wait for the GUI. An early zero exit or a still-running GUI
        # proves neither freshness nor success; the publisher checks the file.
        arguments = [
            sys.executable, str(ROOT / "publish_windows.py"),
            "--input", config["export_path"],
            "--snapshot-dir", config["snapshot_dir"],
            "--repo-dir", config["repo_dir"],
            "--git-path", config["git_path"],
            "--ssh-path", config["ssh_path"],
            "--key-file", config["key_file"],
            "--known-hosts-file", config["known_hosts_file"],
            "--started-at", started_at.isoformat(),
            "--author-name", config["author_name"],
            "--author-email", config["author_email"],
            "--max-age-minutes", "60", "--wait-seconds", "300",
            "--stable-seconds", "5", "--minimum-publish-interval-minutes", "10",
        ]
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, shell=False, check=False)
        if result.returncode != 0:
            raise SyncError("The guarded export/publishing step did not complete.")


def self_test() -> int:
    """Synthetic subprocesses only: no Navicat, Git, key read or network."""
    import tempfile
    import unittest
    from types import SimpleNamespace
    from unittest.mock import patch

    class FakeGui:
        def __init__(self, status):
            self.status = status
        def poll(self):
            return self.status
        def wait(self, *args, **kwargs):
            raise AssertionError("Must never wait for the Navicat GUI.")

    def fixture(base: Path):
        return {"export_path": str(base / "export.json"), "git_path": str(base / "git.exe"),
                "ssh_path": str(base / "ssh.exe"), "key_file": str(base / "private-key"),
                "known_hosts_file": str(base / "known_hosts"), "repo_dir": str(base / "publish-repo"),
                "snapshot_dir": str(base / "snapshots"), "author_name": "Synthetic",
                "author_email": "synthetic@example.invalid"}

    class Tests(unittest.TestCase):
        def test_early_return_and_long_running_gui_both_reach_guarded_publisher(self):
            for status in (0, None):
                with self.subTest(gui_status=status), tempfile.TemporaryDirectory() as directory:
                    config = fixture(Path(directory))
                    with patch("subprocess.Popen", return_value=FakeGui(status)) as launched, \
                         patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as publisher, \
                         patch("time.sleep") as pause:
                        run_sync(config)
                    launched.assert_called_once()
                    self.assertEqual(launched.call_args.args[0], [str(NAVICAT_EXE), "-batchjob", NAVICAT_JOB])
                    self.assertIs(launched.call_args.kwargs["shell"], False)
                    pause.assert_called_once_with(1.1)
                    publisher.assert_called_once()
                    args = publisher.call_args.args[0]
                    stamp = datetime.fromisoformat(args[args.index("--started-at") + 1])
                    self.assertEqual(stamp.utcoffset().total_seconds(), 0)
                    for key, value in (("--wait-seconds", "300"), ("--stable-seconds", "5"),
                                       ("--minimum-publish-interval-minutes", "10")):
                        self.assertEqual(args[args.index(key) + 1], value)
                    self.assertIs(publisher.call_args.kwargs["shell"], False)

        def test_immediate_navicat_failure_does_not_run_publisher(self):
            with tempfile.TemporaryDirectory() as directory:
                with patch("subprocess.Popen", return_value=FakeGui(2)), patch("subprocess.run") as publisher, patch("time.sleep"):
                    with self.assertRaises(SyncError):
                        run_sync(fixture(Path(directory)))
                publisher.assert_not_called()

        def test_publisher_failure_is_failure_without_success_claim(self):
            with tempfile.TemporaryDirectory() as directory:
                config = fixture(Path(directory))
                with patch("subprocess.Popen", return_value=FakeGui(0)), \
                     patch("subprocess.run", return_value=SimpleNamespace(returncode=1)), patch("time.sleep"):
                    with self.assertRaises(SyncError):
                        run_sync(config)
                output, errors = io.StringIO(), io.StringIO()
                with patch(__name__ + ".read_config", return_value=config), \
                     patch(__name__ + ".validate_runtime"), \
                     patch(__name__ + ".run_sync", side_effect=SyncError("synthetic failure")), \
                     patch("sys.stdout", output), patch("sys.stderr", errors):
                    self.assertEqual(main(["--config", str(Path(directory) / "sync-config.json")]), 1)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("未确认网站更新", errors.getvalue())
                self.assertNotIn("synthetic failure", errors.getvalue())

        def test_config_refuses_an_arbitrary_command_or_secret_field(self):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                path = base / "sync-config.json"
                for field in ("navicat_command", "password"):
                    config = fixture(base)
                    config[field] = "synthetic unsupported value"
                    path.write_text(json.dumps(config), encoding="utf-8")
                    with self.assertRaises(SyncError):
                        read_config(path)

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="直接由已验证的 Windows Python 启动本轮 Navicat 查询和快照同步。")
    parser.add_argument("--config", type=Path, help="只有路径和提交署名的 sync-config.json")
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.self_test:
        return self_test()
    if arguments.config is None:
        parser.error("需要 --config sync-config.json。")
    try:
        config = read_config(arguments.config)
        validate_runtime(config)
        run_sync(config)
        print("本轮查询核验与同步步骤已完成；公网网站构建和显示仍需核实。")
        return 0
    except (OSError, ValueError, TypeError, KeyError, SyncError, PublishError, subprocess.SubprocessError):
        print("同步未完成，未确认网站更新。请由维护人员检查本轮导出和发布状态。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
