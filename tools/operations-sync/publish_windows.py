#!/usr/bin/env python3
"""Prepare/validate a fresh Navicat export and publish only four monthly files.

This utility never reads the SSH private key. Git/SSH use that file directly.
Repository, branch, SSH endpoint, output paths and trusted host fingerprint are
fixed. --self-test performs synthetic/local tests, without Git or networking.
The remote machine, Navicat job, PortableGit and deployment key are NOT set up
by this file. A successful push is not a completed GitHub Pages deployment.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any

from convert_snapshot import (
    ALLOWED_MONTHS, MAX_SAFE_INTEGER, OPERATING_STORES_METHOD,
    REGIONS, SnapshotError, aware_time, build_snapshots,
    input_records, load_export, numeric, write_snapshots_atomic,
)

REMOTE_URL = "ssh://git@ssh.github.com:443/starbeef6/yanmingxing-asia.git"
BRANCH = "main"
HOST_NAME = "[ssh.github.com]:443"
# GitHub's public ED25519 host fingerprint, checked against official docs.
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
HOST_FINGERPRINT = "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"
PATHS = tuple(f"assets/operations-data/{month}.json" for month in ALLOWED_MONTHS)
MARKER = "operations-dashboard-publisher.json"
PUBLISH_STATE = "operations-dashboard-last-push.json"
TOP_FIELDS = {"month", "start", "end", "fetchedAt", "source", "sourceComplete",
              "dataQualityWarning", "status", "rows", "targets"}
LEGACY_ROW_FIELDS = {"region", "gmv", "storeDays", "storeAverage", "status"}
CURRENT_ROW_FIELDS = LEGACY_ROW_FIELDS | {"operatingStores", "operatingStoresDate"}


class PublishError(RuntimeError):
    pass


def validate_config_paths(*, export: Path, snapshots: Path, repo: Path,
                          private_key: Path, known_hosts: Path) -> None:
    """Stat/resolve only: prevent a path mix-up from opening or overwriting a key."""
    export, snapshots, repo, private_key, known_hosts = (
        path.resolve() for path in (export, snapshots, repo, private_key, known_hosts)
    )
    if snapshots.is_relative_to(repo) or repo.is_relative_to(snapshots):
        raise PublishError("本地快照输出与专用发布仓库必须使用互不包含的目录。")
    for path in (export, private_key, known_hosts):
        if path.is_relative_to(repo) or path.is_relative_to(snapshots):
            raise PublishError("原始导出、部署密钥及主机校验文件必须位于快照和发布目录之外。")
    for left, right in ((export, private_key), (export, known_hosts), (private_key, known_hosts)):
        if left == right or (left.exists() and right.exists() and os.path.samefile(left, right)):
            raise PublishError("导出、部署密钥及主机校验文件不能指向同一个文件。")


def verify_known_hosts(path: Path) -> None:
    """Read public host keys only. Never accept a key merely from ssh-keyscan."""
    if not path.is_file():
        raise PublishError("缺少已按 GitHub 官方指纹核验的 known_hosts 文件。")
    entries = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    if len(entries) != 1:
        raise PublishError("专用 known_hosts 必须仅含一个已核验的 GitHub ED25519 项。")
    parts = entries[0].split()
    if len(parts) < 3 or parts[0] != HOST_NAME or parts[1] != "ssh-ed25519":
        raise PublishError("known_hosts 的主机、端口或算法不是已核验的 GitHub 入口。")
    try:
        blob = base64.b64decode(parts[2], validate=True)
    except (ValueError, base64.binascii.Error):
        raise PublishError("known_hosts 公钥格式无效。") from None
    observed = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    if observed != HOST_FINGERPRINT:
        raise PublishError("GitHub 主机公钥不匹配官方 ED25519 指纹；停止发布。")


def fresh_payloads(path: Path, started_at: datetime, *, now: datetime,
                   max_age_minutes: int) -> dict[str, dict[str, Any]]:
    # No substitute of file mtime/local conversion time for queried_at.
    if path.stat().st_mtime <= started_at.timestamp():
        raise PublishError("导出文件未在本轮启动后更新。")
    raw = load_export(path)
    for row in input_records(raw):
        stamp = aware_time(row.get("queried_at"), "queried_at")
        if stamp <= started_at:
            raise PublishError("queried_at 不晚于本轮启动；不能发布旧导出。")
    return build_snapshots(raw, ALLOWED_MONTHS, now=now, max_age_minutes=max_age_minutes)


def wait_for_export(path: Path, started_at: datetime, *, wait_seconds: int,
                    stable_seconds: int, max_age_minutes: int) -> dict[str, dict[str, Any]]:
    if wait_seconds <= 0 or stable_seconds < 2 or stable_seconds >= wait_seconds:
        raise PublishError("等待窗口须大于稳定窗口，稳定窗口至少两秒。")
    deadline = time.monotonic() + wait_seconds
    previous = None
    unchanged_since = None
    while time.monotonic() < deadline:
        try:
            stat = path.stat()
            if stat.st_mtime <= started_at.timestamp() or stat.st_size == 0:
                raise PublishError("等待本轮导出文件。")
            # Include content digest: identical sizes alone do not imply stability.
            content = path.read_bytes()
            after = path.stat()
            signature = (after.st_mtime_ns, after.st_size, hashlib.sha256(content).digest())
            if stat.st_mtime_ns != after.st_mtime_ns or stat.st_size != after.st_size:
                signature = None
            if signature != previous:
                previous, unchanged_since = signature, time.monotonic()
            elif signature is not None and time.monotonic() - unchanged_since >= stable_seconds:
                payloads = fresh_payloads(path, started_at, now=datetime.now(timezone.utc),
                                          max_age_minutes=max_age_minutes)
                # Reject a rewrite that took place while JSON validation ran.
                final = path.stat()
                if (final.st_mtime_ns, final.st_size, hashlib.sha256(path.read_bytes()).digest()) == signature:
                    return payloads
                previous, unchanged_since = None, None
        except (OSError, SnapshotError, PublishError, ValueError, TypeError):
            # Partial JSON/partial coverage may be observed during an active export.
            # The bounded timeout preserves all previously successful files.
            previous, unchanged_since = None, None
        time.sleep(1)
    raise PublishError("本轮导出未在等待窗口内变成完整、稳定且通过核验的数据；未发布。")


class Git:
    def __init__(self, git_path: Path, ssh_path: Path, private_key: Path, known_hosts: Path):
        for path, name in ((git_path, "Git"), (ssh_path, "SSH"), (private_key, "部署密钥")):
            if not path.is_file():
                raise PublishError(f"缺少 {name} 文件；本准备版不会安装或生成它。")
        verify_known_hosts(known_hosts)
        self.executable = str(git_path.resolve())
        # Git for Windows invokes core SSH commands through its sh interpreter.
        posix = lambda path: shlex.quote(str(path.resolve()).replace("\\", "/"))
        self.environment = dict(os.environ)
        for name in tuple(self.environment):
            if name.startswith("GIT_") or name.startswith("SSH_"):
                del self.environment[name]
        self.environment.update({
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_SSH_COMMAND": (
                f"{posix(ssh_path)} -F none -o BatchMode=yes -o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=yes -o HostKeyAlgorithms=ssh-ed25519 "
                f"-o UserKnownHostsFile={posix(known_hosts)} -o GlobalKnownHostsFile=none "
                f"-i {posix(private_key)} -p 443"
            ),
        })

    def run(self, repo: Path, *arguments: str, timeout: int = 120) -> str:
        hooks = repo / ".git" / "operations-dashboard-empty-hooks"
        command = [self.executable, "-c", f"core.hooksPath={hooks}", "-c", "credential.helper=",
                   "-c", "protocol.file.allow=never", "-C", str(repo), *arguments]
        try:
            result = subprocess.run(command, env=self.environment, capture_output=True,
                                    encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise PublishError("Git/SSH 步骤未完成；原网站数据保留。") from None
        if result.returncode:
            # Do not echo command/environment, raw stdout/stderr or key contents.
            raise PublishError(f"Git {arguments[0]} 未成功；没有强制推送，原网站数据保留。")
        return result.stdout if "-z" in arguments else result.stdout.strip()


@contextmanager
def publication_lock(repo: Path):
    repo.parent.mkdir(parents=True, exist_ok=True)
    path = repo.parent / (repo.name + ".dashboard-publish.lock")
    # Keep the tiny file: unlinking after unlock can split ownership across two
    # inodes. Kernel-held locks release automatically on crash or restart.
    stream = path.open("a+b")
    acquired = False
    try:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise PublishError("已有发布正在执行；本轮不覆盖。") from None
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def ensure_dedicated_repo(repo: Path, git: Git) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    metadata = repo / ".git"
    expected = {"purpose": "operations-dashboard-snapshot-publisher", "remote": REMOTE_URL, "branch": BRANCH}
    if not metadata.exists():
        if any(repo.iterdir()):
            raise PublishError("仓库目录不是空目录；拒绝初始化或重置其他工作。")
        git.run(repo, "init")
        git.run(repo, "remote", "add", "origin", REMOTE_URL)
        (metadata / MARKER).write_text(json.dumps(expected), encoding="utf-8")
    if not metadata.is_dir() or not (metadata / MARKER).is_file():
        raise PublishError("不是本脚本初始化的专用仓库；拒绝重置现有项目。")
    if json.loads((metadata / MARKER).read_text(encoding="utf-8")) != expected:
        raise PublishError("专用仓库标记不匹配；停止发布。")
    if git.run(repo, "remote", "get-url", "origin") != REMOTE_URL:
        raise PublishError("远端不是指定网站仓库；停止发布。")
    (metadata / "operations-dashboard-empty-hooks").mkdir(exist_ok=True)


def snapshot_period_end(payload: Any, month: str) -> date | None:
    """Validate the metadata needed to compare a new period with a saved one.

    Accept both approved row formats. A malformed prior file must stop a
    publication rather than erase its history by treating it as a first run.
    """
    if (not isinstance(payload, dict) or set(payload) != TOP_FIELDS
            or payload.get("month") != month or payload.get("start") != month + "-01"
            or not isinstance(payload.get("source"), dict)
            or payload["source"].get("mode") != "snapshot"
            or payload.get("targets") != {} or payload.get("sourceComplete") is not False
            or not isinstance(payload.get("rows"), list)
            or any(not isinstance(row, dict) for row in payload["rows"])
            or [row.get("region") for row in payload["rows"]] != list(REGIONS)):
        raise PublishError("快照元数据或六个汇总范围无效；停止覆盖。")
    end_value = payload["end"]
    if end_value is None:
        end = None
    else:
        if not isinstance(end_value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_value):
            raise PublishError("快照截止日期无效；停止覆盖。")
        try:
            end = date.fromisoformat(end_value)
        except ValueError:
            raise PublishError("快照截止日期无效；停止覆盖。") from None
        if end_value[:7] != month:
            raise PublishError("快照截止日期不属于对应月份；停止覆盖。")
    row_schema = None
    for row in payload["rows"]:
        fields = set(row)
        if fields not in (LEGACY_ROW_FIELDS, CURRENT_ROW_FIELDS) or (row_schema is not None and fields != row_schema):
            raise PublishError("快照区域行字段无效；停止覆盖。")
        row_schema = fields
        if row["status"] == "NO_DATA":
            if any(row[field] is not None for field in ("gmv", "storeDays", "storeAverage")):
                raise PublishError("无数据区域含非空实际值；停止覆盖。")
        elif row["status"] == "OK":
            if (end is None or type(row["storeDays"]) is not int
                    or not 0 <= row["storeDays"] <= MAX_SAFE_INTEGER):
                raise PublishError("实际区域的截止日期或营业店天无效；停止覆盖。")
            try:
                numeric(row["gmv"], "已发布 gmv")
                if row["storeAverage"] is None:
                    if row["storeDays"] != 0:
                        raise PublishError("已有营业店天缺少店日均；停止覆盖。")
                else:
                    numeric(row["storeAverage"], "已发布 storeAverage")
            except SnapshotError:
                raise PublishError("快照实际值不是有限数字；停止覆盖。") from None
        else:
            raise PublishError("快照区域状态无效；停止覆盖。")
        if fields == CURRENT_ROW_FIELDS:
            if payload["source"].get("operatingStoresMethod") != OPERATING_STORES_METHOD:
                raise PublishError("快照在营门店数口径无效；停止覆盖。")
            if row["status"] == "NO_DATA":
                if row["operatingStores"] is not None or row["operatingStoresDate"] is not None:
                    raise PublishError("无数据区域含在营门店数；停止覆盖。")
            elif (type(row["operatingStores"]) is not int
                    or not 0 <= row["operatingStores"] <= row["storeDays"]
                    or row["operatingStoresDate"] != end_value):
                raise PublishError("快照在营门店数或统计日期无效；停止覆盖。")
    has_actual = any(row["status"] == "OK" for row in payload["rows"])
    if payload["status"] != ("OK" if has_actual else "NO_DATA"):
        raise PublishError("快照总状态与实际行不一致；停止覆盖。")
    if payload["fetchedAt"] is not None:
        try:
            aware_time(payload["fetchedAt"], "已发布 fetchedAt")
        except SnapshotError:
            raise PublishError("快照查询时间格式无效；停止覆盖。") from None
    elif has_actual:
        raise PublishError("实际快照缺少查询时间；停止覆盖。")
    if "dataThrough" in payload["source"] and payload["source"]["dataThrough"] != end_value:
        raise PublishError("快照来源实际截止日与页面截止日不一致；停止覆盖。")
    return end


def validate_payloads(payloads: dict[str, dict[str, Any]]) -> dict[str, bytes]:
    if set(payloads) != set(ALLOWED_MONTHS):
        raise PublishError("必须一次发布九至十二月四份已验证快照。")
    contents = {}
    batch_row_fields = None
    for month in ALLOWED_MONTHS:
        payload = payloads[month]
        snapshot_period_end(payload, month)
        if (set(payload) != TOP_FIELDS or payload.get("month") != month
                or payload.get("targets") != {} or payload.get("sourceComplete") is not False
                or payload.get("source", {}).get("mode") != "snapshot"
                or [row.get("region") for row in payload.get("rows", [])] != list(REGIONS)):
            raise PublishError("快照结构或公开字段不符合已核准格式。")
        for row in payload["rows"]:
            row_fields = set(row)
            if row_fields not in (LEGACY_ROW_FIELDS, CURRENT_ROW_FIELDS):
                raise PublishError("区域行含未核准字段或缺少必需字段。")
            if batch_row_fields is None:
                batch_row_fields = row_fields
            elif row_fields != batch_row_fields:
                raise PublishError("四个月六个范围必须统一使用同一版区域行格式。")
            if row_fields == CURRENT_ROW_FIELDS:
                if payload["source"].get("operatingStoresMethod") != OPERATING_STORES_METHOD:
                    raise PublishError("在营门店数未使用已核准的截止日正营业额口径。")
                stores, stores_date = row["operatingStores"], row["operatingStoresDate"]
                if row["status"] == "NO_DATA":
                    if stores is not None or stores_date is not None:
                        raise PublishError("无实际数据的区域不能发布在营门店数或统计日期。")
                elif row["status"] == "OK":
                    cumulative_days = row["storeDays"]
                    if (type(stores) is not int or not 0 <= stores <= MAX_SAFE_INTEGER
                            or type(cumulative_days) is not int
                            or not 0 <= cumulative_days <= MAX_SAFE_INTEGER
                            or stores > cumulative_days):
                        raise PublishError("截止日在营门店数须为有效非负整数，且不超过累计营业店天。")
                    if (not isinstance(stores_date, str)
                            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stores_date)
                            or stores_date != payload["end"]):
                        raise PublishError("在营门店数日期须等于快照实际截止营业日。")
                    try:
                        datetime.strptime(stores_date, "%Y-%m-%d")
                    except ValueError:
                        raise PublishError("在营门店数日期不是有效营业日期。") from None
                else:
                    raise PublishError("区域行状态无效。")
        contents[f"assets/operations-data/{month}.json"] = (
            json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        ).encode("utf-8")
    return contents


def safe_destination(repo: Path, relative: str) -> Path:
    destination = repo / relative
    for component in (destination, *destination.parents):
        if component == repo.parent:
            break
        if component.is_symlink():
            raise PublishError("发布路径含符号链接；停止写入。")
    if not destination.resolve().is_relative_to(repo.resolve()):
        raise PublishError("发布路径超出专用仓库。")
    return destination


def prevent_period_regression(payloads: dict[str, dict[str, Any]], repo: Path) -> None:
    """Check every latest-main snapshot before any new monthly file is written."""
    for month in ALLOWED_MONTHS:
        previous_path = safe_destination(repo, f"assets/operations-data/{month}.json")
        if not previous_path.exists():
            continue
        try:
            previous = load_export(previous_path)
            previous_end = snapshot_period_end(previous, month)
        except (OSError, ValueError, TypeError, SnapshotError):
            raise PublishError("已发布快照无法安全核验；停止覆盖全部月份。") from None
        new_end = snapshot_period_end(payloads[month], month)
        if previous_end is not None and (new_end is None or new_end < previous_end):
            raise PublishError("本轮实际截止日早于已发布结果；保留上次成功数据。")
        for old_row, new_row in zip(previous["rows"], payloads[month]["rows"]):
            if old_row["status"] == "OK" and new_row["status"] != "OK":
                raise PublishError("本轮无数据不能替代已发布区域实际；保留上次成功数据。")


def publish_payloads(payloads: dict[str, dict[str, Any]], repo: Path, git: Git, *,
                     author_name: str, author_email: str, minimum_interval_minutes: int = 10,
                     now: datetime | None = None) -> tuple[str, str | None]:
    contents = validate_payloads(payloads)
    if minimum_interval_minutes < 10:
        raise PublishError("当前 Pages 分支构建至少间隔十分钟，避免超过每小时构建软限制。")
    if not author_name.strip() or not re.fullmatch(r"[^\s<>]+@[^\s<>]+", author_email):
        raise PublishError("请配置已核准的提交署名及 GitHub 关联提交邮箱。")
    fixed_test_clock = now is not None
    now = now or datetime.now(timezone.utc)
    ensure_dedicated_repo(repo, git)
    state_path = repo / ".git" / PUBLISH_STATE
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        previous = aware_time(state.get("pushedAt"), "上次推送时间")
        if previous > now:
            raise PublishError("上次推送时间晚于当前时间；停止发布。")
        interval_deadline = previous + timedelta(minutes=minimum_interval_minutes)
        remaining_seconds = (interval_deadline - now).total_seconds()
        if remaining_seconds > 0:
            # Ten-minute task starts may precede ten minutes since the previous
            # push finished by a few seconds. Keep the existing publication
            # lock and wait only for this bounded scheduling alignment.
            # Explicit test clocks retain immediate rejection, never sleeping.
            if fixed_test_clock or remaining_seconds > 60:
                raise PublishError("距上次成功推送不足规定间隔；本轮不触发重复构建。")
            time.sleep(remaining_seconds)
            now = datetime.now(timezone.utc)
            if now < interval_deadline:
                raise PublishError("等待后仍未达到规定推送间隔；停止发布。")
    # Only previous failed writes of these four paths may be discarded. Never
    # clean/reset a workspace with other changed or untracked files.
    dirty = git.run(repo, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    for entry in dirty.split("\0"):
        if entry and (len(entry) < 4 or entry[3:] not in PATHS or "R" in entry[:2] or "C" in entry[:2]):
            raise PublishError("专用仓库含非快照改动；拒绝重置或覆盖。")
    git.run(repo, "fetch", "--no-tags", "origin", BRANCH)
    latest = git.run(repo, "rev-parse", f"refs/remotes/origin/{BRANCH}")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", latest):
        raise PublishError("最新远端提交格式无效。")
    git.run(repo, "reset", "--hard", latest)
    prevent_period_regression(payloads, repo)
    for relative, body in contents.items():
        destination = safe_destination(repo, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".od-snapshot-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    git.run(repo, "add", "--", *PATHS)
    changed = git.run(repo, "diff", "--cached", "--name-only", "-z")
    changed_paths = {path for path in changed.split("\0") if path}
    if not changed_paths:
        return "unchanged", None
    if not changed_paths.issubset(set(PATHS)):
        raise PublishError("暂存内容含未批准路径；停止提交。")
    git.run(repo, "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
            "commit", "-m", "Update validated operating dashboard snapshots")
    commit = git.run(repo, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise PublishError("本地提交格式无效。")
    # Rejected/non-fast-forward pushes fail. The next fresh scheduled run
    # fetches latest main and overlays only the permitted monthly snapshots.
    git.run(repo, "push", "origin", f"HEAD:refs/heads/{BRANCH}")
    pushed_at = now if fixed_test_clock else datetime.now(timezone.utc)
    state_path.write_text(json.dumps({"pushedAt": pushed_at.isoformat(), "commit": commit}), encoding="utf-8")
    return "pushed", commit


def self_test() -> int:
    """Meaningful synthetic regression checks; no external executables/network."""
    from copy import deepcopy
    import unittest
    from unittest.mock import patch

    clock = datetime.fromisoformat("2026-10-10T04:00:05+00:00")
    started = datetime.fromisoformat("2026-10-10T04:00:01+00:00")

    def fixture():
        records = []
        from convert_snapshot import expected_period
        for month in ALLOWED_MONTHS:
            start, end = expected_period(month, clock)
            if end is None:
                continue
            for offset in range((end - start).days + 1):
                for region in REGIONS:
                    records.append({"business_date": (start + timedelta(days=offset)).isoformat(),
                                    "region": region, "gmv": "10.25", "store_days": 1,
                                    "source_rows": 1, "queried_at": "2026-10-10T04:00:02Z"})
        return records

    class FakeGit:
        def __init__(self, changed=True, fail_push=False, dirty="", on_reset=None):
            self.calls, self.changed, self.fail_push, self.dirty = [], changed, fail_push, dirty
            self.on_reset = on_reset
        def run(self, repo, *args, **kwargs):
            self.calls.append(args)
            if args[:1] == ("init",):
                (repo / ".git").mkdir()
            if args[:2] == ("remote", "get-url"):
                return REMOTE_URL
            if args[:1] == ("status",):
                return self.dirty
            if args[:1] == ("rev-parse",):
                return "a" * 40 if args[-1] != "HEAD" else "b" * 40
            if args[:1] == ("diff",):
                return "\0".join(PATHS) + "\0" if self.changed else ""
            if args[:1] == ("reset",) and self.on_reset is not None:
                self.on_reset(repo)
            if args[:1] == ("push",) and self.fail_push:
                raise PublishError("synthetic rejected push")
            return ""

    class Tests(unittest.TestCase):
        def test_fresh_query_accepts_current_source_lag_with_actual_cutoff(self):
            early_clock = datetime.fromisoformat("2026-10-10T18:08:00+00:00")
            early_started = datetime.fromisoformat("2026-10-10T18:06:50+00:00")
            for cutoff in ("2026-10-09", "2026-10-08"):
                data = [row for row in fixture() if row["business_date"] <= cutoff]
                for row in data:
                    row["queried_at"] = "2026-10-10T18:06:58Z"
                with self.subTest(cutoff=cutoff), tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    exported = base / "export.json"
                    exported.write_text(json.dumps(data), encoding="utf-8")
                    os.utime(exported, (early_clock.timestamp(), early_clock.timestamp()))
                    payloads = fresh_payloads(exported, early_started, now=early_clock, max_age_minutes=60)
                    self.assertEqual(payloads["2026-10"]["end"], cutoff)
                    self.assertEqual(payloads["2026-10"]["source"]["expectedThrough"], "2026-10-10")
                    result, _ = publish_payloads(payloads, base / "site", FakeGit(),
                                                 author_name="Synthetic", author_email="synthetic@example.invalid",
                                                 now=early_clock)
                    self.assertEqual(result, "pushed")
                    published = json.loads((base / "site" / PATHS[1]).read_text(encoding="utf-8"))
                    self.assertEqual(published["end"], cutoff)
                    self.assertEqual(published["rows"][0]["operatingStoresDate"], cutoff)
        def test_latest_main_cutoff_regression_rejects_before_any_monthly_write(self):
            latest = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            incoming_data = [row for row in fixture() if row["business_date"] != "2026-10-09"]
            incoming_data[0]["gmv"] = "999.25"  # Even this earlier month must remain untouched.
            incoming = build_snapshots(incoming_data, ALLOWED_MONTHS, now=clock)
            saved = {}
            def reset_to_latest(repo):
                for relative, body in validate_payloads(latest).items():
                    target = repo / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                    saved[relative] = body
            with tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / "site"
                git = FakeGit(on_reset=reset_to_latest)
                with patch("os.replace", side_effect=AssertionError("must reject before writing snapshots")):
                    with self.assertRaisesRegex(PublishError, "截止日"):
                        publish_payloads(incoming, repo, git, author_name="Synthetic",
                                         author_email="synthetic@example.invalid", now=clock)
                self.assertEqual(saved, {relative: (repo / relative).read_bytes() for relative in PATHS})
                self.assertFalse(any(call[0] in ("add", "push") or "commit" in call for call in git.calls))
                self.assertTrue(any(call[0] == "fetch" for call in git.calls))
                self.assertTrue(any(call[0] == "reset" for call in git.calls))
        def test_current_no_data_cannot_replace_previously_published_actual(self):
            latest = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            absent_data = [row for row in fixture() if row["business_date"].startswith("2026-09")]
            zero_source_data = fixture()
            for row in zero_source_data:
                row.update(gmv="0", store_days=0, source_rows=0)
            for data in (absent_data, zero_source_data):
                incoming = build_snapshots(data, ALLOWED_MONTHS, now=clock)
                saved = {}
                def reset_to_latest(repo):
                    for relative, body in validate_payloads(latest).items():
                        target = repo / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(body)
                        saved[relative] = body
                with tempfile.TemporaryDirectory() as directory:
                    repo, git = Path(directory) / "site", FakeGit(on_reset=reset_to_latest)
                    with patch("os.replace", side_effect=AssertionError("must preserve published actual")):
                        with self.assertRaises(PublishError):
                            publish_payloads(incoming, repo, git, author_name="Synthetic",
                                             author_email="synthetic@example.invalid", now=clock)
                    self.assertEqual(saved, {relative: (repo / relative).read_bytes() for relative in PATHS})
                    self.assertFalse(any(call[0] in ("add", "push") or "commit" in call for call in git.calls))
        def test_malformed_prior_snapshot_stops_all_months_before_writing(self):
            latest = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            invalid_date = deepcopy(latest["2026-10"])
            invalid_date["end"] = "2026-10-99"
            missing_end = deepcopy(latest["2026-10"])
            missing_end.pop("end")
            invalid_row = deepcopy(latest["2026-10"])
            invalid_row["rows"][0]["storeDays"] = -1
            cases = (b"[", b"[]", json.dumps(missing_end).encode(),
                     json.dumps(invalid_date).encode(), json.dumps(invalid_row).encode())
            for corrupt in cases:
                saved = {}
                def reset_to_latest(repo):
                    for relative, body in validate_payloads(latest).items():
                        target = repo / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        body = corrupt if relative == PATHS[1] else body
                        target.write_bytes(body)
                        saved[relative] = body
                with self.subTest(corrupt=corrupt[:30]), tempfile.TemporaryDirectory() as directory:
                    repo, git = Path(directory) / "site", FakeGit(on_reset=reset_to_latest)
                    with patch("os.replace", side_effect=AssertionError("must reject malformed prior snapshot")):
                        with self.assertRaises(PublishError):
                            publish_payloads(latest, repo, git, author_name="Synthetic",
                                             author_email="synthetic@example.invalid", now=clock)
                    self.assertEqual(saved, {relative: (repo / relative).read_bytes() for relative in PATHS})
                    self.assertFalse(any(call[0] in ("add", "push") or "commit" in call for call in git.calls))
        def test_same_cutoff_backfill_can_replace_amount_and_keep_store_count(self):
            previous = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            updated_data = fixture()
            updated_data[-54]["gmv"] = "20.25"  # October 1 nationwide, not its cutoff date.
            incoming = build_snapshots(updated_data, ALLOWED_MONTHS, now=clock)
            def reset_to_latest(repo):
                for relative, body in validate_payloads(previous).items():
                    target = repo / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
            with tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / "site"
                result, _ = publish_payloads(incoming, repo, FakeGit(on_reset=reset_to_latest),
                                             author_name="Synthetic", author_email="synthetic@example.invalid", now=clock)
                self.assertEqual(result, "pushed")
                updated = json.loads((repo / PATHS[1]).read_text(encoding="utf-8"))
                self.assertEqual(updated["end"], previous["2026-10"]["end"])
                self.assertEqual(updated["rows"][0]["gmv"], previous["2026-10"]["rows"][0]["gmv"] + 10)
                self.assertEqual(updated["rows"][0]["operatingStores"], 1)
        def test_prior_legacy_snapshot_allows_new_count_schema_upgrade(self):
            previous = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            for payload in previous.values():
                for row in payload["rows"]:
                    row.pop("operatingStores")
                    row.pop("operatingStoresDate")
                payload["source"].pop("operatingStoresMethod")
                payload["source"].pop("operatingStoresDefinition")
            def reset_to_latest(repo):
                for relative, body in validate_payloads(previous).items():
                    target = repo / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
            with tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / "site"
                result, _ = publish_payloads(build_snapshots(fixture(), ALLOWED_MONTHS, now=clock), repo,
                                             FakeGit(on_reset=reset_to_latest), author_name="Synthetic",
                                             author_email="synthetic@example.invalid", now=clock)
                self.assertEqual(result, "pushed")
                self.assertEqual(json.loads((repo / PATHS[0]).read_text(encoding="utf-8"))["rows"][0]["operatingStores"], 1)
        def test_legacy_all_five_field_rows_remain_accepted(self):
            payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            for payload in payloads.values():
                payload["source"].pop("operatingStoresMethod")
                payload["source"].pop("operatingStoresDefinition")
                for row in payload["rows"]:
                    row.pop("operatingStores")
                    row.pop("operatingStoresDate")
            self.assertEqual(set(validate_payloads(payloads)), set(PATHS))
        def test_current_rows_publish_cutoff_count_and_future_nulls(self):
            payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            result = validate_payloads(payloads)
            september = json.loads(result[PATHS[0]])
            for row in september["rows"]:
                self.assertEqual(row["storeDays"], 30)
                self.assertEqual(row["operatingStores"], 1)
                self.assertEqual(row["operatingStoresDate"], "2026-09-30")
            november = json.loads(result[PATHS[2]])
            self.assertTrue(all(row["operatingStores"] is None and row["operatingStoresDate"] is None
                                for row in november["rows"]))
        def test_unsafe_operating_store_counts_are_rejected_before_git(self):
            for value in (-1, 1.5, True, 2**53, "1", None, 31):
                payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
                payloads["2026-09"]["rows"][0]["operatingStores"] = value
                git = FakeGit()
                with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                    with self.assertRaises(PublishError):
                        publish_payloads(payloads, Path(directory) / "site", git,
                                         author_name="Synthetic", author_email="synthetic@example.invalid", now=clock)
                    self.assertEqual(git.calls, [])
        def test_cutoff_store_date_must_be_valid_and_match_period_end(self):
            for value in ("2026-09-29", "2026-10-09", "20260930", None):
                payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
                payloads["2026-09"]["rows"][0]["operatingStoresDate"] = value
                with self.subTest(value=value), self.assertRaises(PublishError):
                    validate_payloads(payloads)
            payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            payloads["2026-09"]["end"] = "2026-09-31"
            for row in payloads["2026-09"]["rows"]:
                row["operatingStoresDate"] = "2026-09-31"
            with self.assertRaises(PublishError):
                validate_payloads(payloads)
        def test_partial_region_or_month_upgrade_is_rejected(self):
            original = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            one_region = deepcopy(original)
            one_region["2026-09"]["rows"][1].pop("operatingStores")
            one_region["2026-09"]["rows"][1].pop("operatingStoresDate")
            one_month = deepcopy(original)
            for row in one_month["2026-09"]["rows"]:
                row.pop("operatingStores")
                row.pop("operatingStoresDate")
            for payloads in (one_region, one_month):
                with self.assertRaises(PublishError):
                    validate_payloads(payloads)
        def test_no_data_rows_reject_any_store_count_or_date(self):
            for field, value in (("operatingStores", 0), ("operatingStoresDate", "2026-11-01")):
                payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
                payloads["2026-11"]["rows"][0][field] = value
                with self.subTest(field=field), self.assertRaises(PublishError):
                    validate_payloads(payloads)
        def test_unapproved_store_method_or_row_fields_are_rejected(self):
            for value in (None, "period-distinct-store-count", "cumulative-store-days"):
                payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
                payloads["2026-09"]["source"]["operatingStoresMethod"] = value
                with self.subTest(value=value), self.assertRaises(PublishError):
                    validate_payloads(payloads)
            payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            payloads["2026-09"]["rows"][0]["storeName"] = "unapproved synthetic field"
            with self.assertRaises(PublishError):
                validate_payloads(payloads)
        def test_lock_file_survives_but_does_not_block_recovery(self):
            with tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / "site"
                lock_path = repo.parent / (repo.name + ".dashboard-publish.lock")
                with publication_lock(repo):
                    with self.assertRaises(PublishError):
                        with publication_lock(repo):
                            self.fail("concurrent publication must be rejected")
                self.assertTrue(lock_path.is_file())
                with publication_lock(repo):
                    self.assertTrue(lock_path.is_file())
        def test_process_crash_releases_lock_without_deleting_file(self):
            with tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / "site"
                program = (
                    "import os,sys; from pathlib import Path; "
                    "from publish_windows import publication_lock; "
                    "handle=publication_lock(Path(sys.argv[1])); "
                    "handle.__enter__(); os._exit(0)"
                )
                child = subprocess.run([sys.executable, "-B", "-c", program, str(repo)],
                                       cwd=Path(__file__).resolve().parent, capture_output=True, timeout=10)
                self.assertEqual(child.returncode, 0)
                self.assertTrue((repo.parent / (repo.name + ".dashboard-publish.lock")).is_file())
                with publication_lock(repo):
                    pass
        def test_path_mixup_rejected_without_opening_private_key(self):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                key = base / "private-key"
                key.write_text("synthetic sentinel; never read")
                with patch.object(Path, "read_text", side_effect=AssertionError("must not read")), \
                     patch.object(Path, "read_bytes", side_effect=AssertionError("must not read")):
                    with self.assertRaises(PublishError):
                        validate_config_paths(export=key, snapshots=base / "snapshots", repo=base / "repo",
                                              private_key=key, known_hosts=base / "known_hosts")
                with self.assertRaises(PublishError):
                    validate_config_paths(export=base / "export.json", snapshots=base / "repo" / "snapshots",
                                          repo=base / "repo", private_key=key, known_hosts=base / "known_hosts")
        def test_fresh_export_and_old_query_rejected(self):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "export.json"
                data = fixture()
                path.write_text(json.dumps(data), encoding="utf-8")
                os.utime(path, (clock.timestamp(), clock.timestamp()))
                self.assertEqual(set(fresh_payloads(path, started, now=clock, max_age_minutes=60)), set(ALLOWED_MONTHS))
                data[0]["queried_at"] = started.isoformat()
                path.write_text(json.dumps(data), encoding="utf-8")
                os.utime(path, (clock.timestamp(), clock.timestamp()))
                with self.assertRaises(PublishError):
                    fresh_payloads(path, started, now=clock, max_age_minutes=60)
        def test_stale_mtime_rejected_even_if_query_looks_new(self):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "export.json"
                path.write_text(json.dumps(fixture()), encoding="utf-8")
                os.utime(path, (started.timestamp(), started.timestamp()))
                with self.assertRaises(PublishError):
                    fresh_payloads(path, started, now=clock, max_age_minutes=60)
        def test_bad_knownhost_stops(self):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "known_hosts"
                path.write_text(f"{HOST_NAME} ssh-ed25519 {base64.b64encode(b'not-github').decode()}\n")
                with self.assertRaises(PublishError):
                    verify_known_hosts(path)
        def test_knownhost_fingerprint_and_host_are_both_checked(self):
            blob = b"synthetic public host key"
            fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "known_hosts"
                encoded = base64.b64encode(blob).decode()
                path.write_text(f"{HOST_NAME} ssh-ed25519 {encoded}\n")
                with patch(__name__ + ".HOST_FINGERPRINT", fingerprint):
                    verify_known_hosts(path)
                    path.write_text(f"github.com ssh-ed25519 {encoded}\n")
                    with self.assertRaises(PublishError):
                        verify_known_hosts(path)
        def test_partial_export_waits_for_complete_stable_file(self):
            from datetime import datetime as ActualDatetime
            class FixedDatetime(ActualDatetime):
                @classmethod
                def now(cls, tz=None):
                    return clock if tz is not None else clock.replace(tzinfo=None)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "export.json"
                path.write_text("[", encoding="utf-8")
                os.utime(path, (clock.timestamp(), clock.timestamp()))
                elapsed = [0]
                def pause(seconds):
                    elapsed[0] += seconds
                    if elapsed[0] == 1:
                        path.write_text(json.dumps(fixture()), encoding="utf-8")
                        os.utime(path, (clock.timestamp(), clock.timestamp()))
                with patch(__name__ + ".datetime", FixedDatetime), \
                     patch("time.monotonic", side_effect=lambda: elapsed[0]), patch("time.sleep", side_effect=pause):
                    payloads = wait_for_export(path, started, wait_seconds=8, stable_seconds=2, max_age_minutes=60)
                self.assertEqual(set(payloads), set(ALLOWED_MONTHS))
                self.assertGreaterEqual(elapsed[0], 3)
        def test_invalid_export_timeout_does_not_enter_git_or_change_success_files(self):
            import io
            with tempfile.TemporaryDirectory() as directory:
                base, git = Path(directory), FakeGit()
                output = base / "snapshots"
                output.mkdir()
                previous = output / "2026-09.json"
                previous.write_text("synthetic prior successful snapshot")
                args = ["--input", str(base / "export.json"), "--snapshot-dir", str(output),
                        "--repo-dir", str(base / "repo"), "--git-path", str(base / "git"),
                        "--ssh-path", str(base / "ssh"), "--key-file", str(base / "key"),
                        "--known-hosts-file", str(base / "known_hosts"), "--started-at", started.isoformat(),
                        "--author-name", "Synthetic", "--author-email", "synthetic@example.invalid"]
                with patch(__name__ + ".Git", return_value=git), \
                     patch(__name__ + ".wait_for_export", side_effect=PublishError("synthetic incomplete export")), \
                     patch("sys.stderr", io.StringIO()):
                    self.assertEqual(main(args), 1)
                self.assertEqual(previous.read_text(encoding="utf-8"), "synthetic prior successful snapshot")
                self.assertEqual(git.calls, [])
        def test_single_commit_exact_paths_and_no_force(self):
            with tempfile.TemporaryDirectory() as directory:
                repo, git = Path(directory) / "site", FakeGit()
                payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
                result, commit = publish_payloads(payloads, repo, git, author_name="Synthetic", author_email="synthetic@example.invalid", now=clock)
                self.assertEqual((result, commit), ("pushed", "b" * 40))
                self.assertEqual(next(call for call in git.calls if call[0] == "add"), ("add", "--", *PATHS))
                self.assertEqual(sum("commit" in call for call in git.calls), 1)
                self.assertEqual(next(call for call in git.calls if call[0] == "reset"), ("reset", "--hard", "a" * 40))
                self.assertFalse(any("--force" in call or "-f" in call for call in git.calls))
        def test_unchanged_does_not_commit_or_push(self):
            with tempfile.TemporaryDirectory() as directory:
                git = FakeGit(changed=False)
                result, _ = publish_payloads(build_snapshots(fixture(), ALLOWED_MONTHS, now=clock), Path(directory) / "site", git,
                                             author_name="Synthetic", author_email="synthetic@example.invalid", now=clock)
                self.assertEqual(result, "unchanged")
                self.assertFalse(any(call[0] == "push" or "commit" in call for call in git.calls))
        def test_nonfastforward_leaves_last_success_marker_unchanged(self):
            with tempfile.TemporaryDirectory() as directory:
                repo, git = Path(directory) / "site", FakeGit()
                ensure_dedicated_repo(repo, git)
                state = repo / ".git" / PUBLISH_STATE
                old = json.dumps({"pushedAt": (clock - timedelta(hours=1)).isoformat(), "commit": "c" * 40})
                state.write_text(old)
                git.fail_push = True
                with self.assertRaises(PublishError):
                    publish_payloads(build_snapshots(fixture(), ALLOWED_MONTHS, now=clock), repo, git,
                                     author_name="Synthetic", author_email="synthetic@example.invalid", now=clock)
                self.assertEqual(state.read_text(encoding="utf-8"), old)
        def test_real_clock_short_schedule_gap_waits_then_publishes(self):
            from datetime import datetime as ActualDatetime
            for missing_seconds in (14, 60):
                with self.subTest(missing_seconds=missing_seconds), tempfile.TemporaryDirectory() as directory:
                    repo, git = Path(directory) / "site", FakeGit()
                    ensure_dedicated_repo(repo, git)
                    previous = clock - timedelta(minutes=10) + timedelta(seconds=missing_seconds)
                    state = repo / ".git" / PUBLISH_STATE
                    state.write_text(json.dumps({"pushedAt": previous.isoformat(), "commit": "c" * 40}))
                    current = [clock]
                    class MovingDatetime(ActualDatetime):
                        @classmethod
                        def now(cls, tz=None):
                            return current[0] if tz is not None else current[0].replace(tzinfo=None)
                    def pause(seconds):
                        self.assertEqual(seconds, missing_seconds)
                        current[0] += timedelta(seconds=seconds)
                    payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
                    with patch(__name__ + ".datetime", MovingDatetime), patch("time.sleep", side_effect=pause) as sleeper:
                        result, commit = publish_payloads(payloads, repo, git, author_name="Synthetic",
                                                          author_email="synthetic@example.invalid")
                    self.assertEqual((result, commit), ("pushed", "b" * 40))
                    sleeper.assert_called_once_with(missing_seconds)
                    recorded = aware_time(json.loads(state.read_text(encoding="utf-8"))["pushedAt"], "pushedAt")
                    self.assertGreaterEqual(recorded - previous, timedelta(minutes=10))
        def test_real_clock_early_or_future_state_rejects_without_sleeping(self):
            from datetime import datetime as ActualDatetime
            class FixedDatetime(ActualDatetime):
                @classmethod
                def now(cls, tz=None):
                    return clock if tz is not None else clock.replace(tzinfo=None)
            for previous in (clock - timedelta(minutes=9) + timedelta(seconds=1),
                             clock + timedelta(seconds=1), clock + timedelta(days=1)):
                with self.subTest(previous=previous), tempfile.TemporaryDirectory() as directory:
                    repo, git = Path(directory) / "site", FakeGit()
                    ensure_dedicated_repo(repo, git)
                    state = repo / ".git" / PUBLISH_STATE
                    old = json.dumps({"pushedAt": previous.isoformat(), "commit": "c" * 40})
                    state.write_text(old)
                    with patch(__name__ + ".datetime", FixedDatetime), patch("time.sleep") as sleeper:
                        with self.assertRaises(PublishError):
                            publish_payloads(build_snapshots(fixture(), ALLOWED_MONTHS, now=clock), repo, git,
                                             author_name="Synthetic", author_email="synthetic@example.invalid")
                    sleeper.assert_not_called()
                    self.assertEqual(state.read_text(encoding="utf-8"), old)
                    self.assertFalse(any(call[0] in ("fetch", "reset", "push") for call in git.calls))
        def test_fixed_test_clock_or_unadvanced_real_clock_cannot_bypass_interval(self):
            from datetime import datetime as ActualDatetime
            class FixedDatetime(ActualDatetime):
                @classmethod
                def now(cls, tz=None):
                    return clock if tz is not None else clock.replace(tzinfo=None)
            for explicit_clock in (True, False):
                with self.subTest(explicit_clock=explicit_clock), tempfile.TemporaryDirectory() as directory:
                    repo, git = Path(directory) / "site", FakeGit()
                    ensure_dedicated_repo(repo, git)
                    state = repo / ".git" / PUBLISH_STATE
                    old = json.dumps({"pushedAt": (clock - timedelta(minutes=10) + timedelta(seconds=14)).isoformat(),
                                      "commit": "c" * 40})
                    state.write_text(old)
                    with patch(__name__ + ".datetime", FixedDatetime), patch("time.sleep") as sleeper:
                        with self.assertRaises(PublishError):
                            publish_payloads(build_snapshots(fixture(), ALLOWED_MONTHS, now=clock), repo, git,
                                             author_name="Synthetic", author_email="synthetic@example.invalid",
                                             now=clock if explicit_clock else None)
                    if explicit_clock:
                        sleeper.assert_not_called()
                    else:
                        sleeper.assert_called_once_with(14)
                    self.assertEqual(state.read_text(encoding="utf-8"), old)
                    self.assertFalse(any(call[0] in ("fetch", "reset", "push") for call in git.calls))
        def test_unowned_directory_and_unrelated_edits_are_not_reset(self):
            with tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / "other"
                repo.mkdir()
                (repo / "my-work.txt").write_text("keep")
                git = FakeGit()
                with self.assertRaises(PublishError):
                    ensure_dedicated_repo(repo, git)
                self.assertEqual((repo / "my-work.txt").read_text(encoding="utf-8"), "keep")
                repo = Path(directory) / "site"
                git = FakeGit(dirty=" M index.html\0")
                with self.assertRaises(PublishError):
                    publish_payloads(build_snapshots(fixture(), ALLOWED_MONTHS, now=clock), repo, git,
                                     author_name="Synthetic", author_email="synthetic@example.invalid", now=clock)
                self.assertFalse(any(call[0] == "reset" for call in git.calls))
        def test_unapproved_payload_fails_before_any_git_step(self):
            payloads = build_snapshots(fixture(), ALLOWED_MONTHS, now=clock)
            payloads["2026-09"]["extra"] = "synthetic unapproved field"
            git = FakeGit()
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(PublishError):
                    publish_payloads(payloads, Path(directory) / "site", git,
                                     author_name="Synthetic", author_email="synthetic@example.invalid", now=clock)
                self.assertEqual(git.calls, [])

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="仅发布经过本轮验证的全国及五区月快照；不安装、不建凭据。")
    parser.add_argument("--self-test", action="store_true")
    for name in ("input", "snapshot-dir", "repo-dir", "git-path", "ssh-path", "key-file", "known-hosts-file"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--started-at", help="本轮启动时点，必须含时区，不能使用历史固定时间")
    parser.add_argument("--author-name")
    parser.add_argument("--author-email")
    parser.add_argument("--max-age-minutes", type=int, default=60)
    parser.add_argument("--wait-seconds", type=int, default=300)
    parser.add_argument("--stable-seconds", type=int, default=5)
    parser.add_argument("--minimum-publish-interval-minutes", type=int, default=10)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if any(getattr(args, name) is None for name in ("input", "snapshot_dir", "repo_dir", "git_path", "ssh_path",
                                                   "key_file", "known_hosts_file", "started_at", "author_name", "author_email")):
        parser.error("正式同步须提供全部文件路径、启动时间和提交署名；参见 --help。")
    try:
        started = aware_time(args.started_at, "--started-at")
        if started > datetime.now(timezone.utc):
            raise PublishError("启动时间晚于当前时间，不能发布。")
        validate_config_paths(export=args.input, snapshots=args.snapshot_dir, repo=args.repo_dir,
                              private_key=args.key_file, known_hosts=args.known_hosts_file)
        git = Git(args.git_path, args.ssh_path, args.key_file, args.known_hosts_file)
        with publication_lock(args.repo_dir.resolve()):
            payloads = wait_for_export(args.input, started, wait_seconds=args.wait_seconds,
                                       stable_seconds=args.stable_seconds, max_age_minutes=args.max_age_minutes)
            write_snapshots_atomic(payloads, args.snapshot_dir)
            status, commit = publish_payloads(payloads, args.repo_dir.resolve(), git,
                                              author_name=args.author_name, author_email=args.author_email,
                                              minimum_interval_minutes=args.minimum_publish_interval_minutes)
        if status == "unchanged":
            print("已核验：四个月快照无变化，没有提交或推送。")
        else:
            print(f"已推送快照提交：{commit}；GitHub Pages 构建及公网显示仍需另行验证。")
        return 0
    except (OSError, ValueError, TypeError, PublishError, SnapshotError):
        # Never show export contents, command lines, authentication or raw errors.
        print("同步未完成，未宣称网站已更新；保留网站上次成功数据。请核对本轮导出、专用仓库和发布连接。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
