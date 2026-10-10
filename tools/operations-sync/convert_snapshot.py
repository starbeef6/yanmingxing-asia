#!/usr/bin/env python3
"""Convert aggregate-only Navicat JSON into validated monthly dashboard snapshots.

This preparation utility does not query databases, retrieve credentials or upload
files. All actual amounts and denominators must be supplied by the input export.
"""

from __future__ import annotations

import argparse
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


BUSINESS_ZONE = timezone(timedelta(hours=8))
REGIONS = ("全国", "北区", "西区", "南区", "四川地区", "江苏地区")
ALLOWED_MONTHS = ("2026-09", "2026-10", "2026-11", "2026-12")
INPUT_FIELDS = {"business_date", "region", "gmv", "store_days", "queried_at", "source_rows"}
MAX_SAFE_INTEGER = 2**53 - 1
QUALITY_WARNING = "Navicat 定时查询快照；综合表口径、全国与五区及历史补录覆盖尚未完成现场复核。"


class SnapshotError(ValueError):
    """Input cannot safely replace the previously accepted snapshot."""


def aware_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotError(f"{field} 必须是带时区的 ISO 时间。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SnapshotError(f"{field} 不是有效的 ISO 时间。") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotError(f"{field} 缺少时区；不能猜测数据库会话时区。")
    return parsed


def numeric(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float, Decimal)):
        raise SnapshotError(f"{field} 必须是有限数字。")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        raise SnapshotError(f"{field} 必须是有限数字。") from None
    if not parsed.is_finite():
        raise SnapshotError(f"{field} 必须是有限数字。")
    return parsed


def count(value: Any, field: str) -> int:
    parsed = numeric(value, field)
    if parsed < 0 or parsed != parsed.to_integral_value() or parsed > MAX_SAFE_INTEGER:
        raise SnapshotError(f"{field} 必须是不超过网页精度上限的非负整数。")
    return int(parsed)


def json_number(value: Decimal, field: str) -> float:
    try:
        converted = float(value)
    except (ValueError, OverflowError):
        raise SnapshotError(f"{field} 超出网页可显示数字范围。") from None
    if not math.isfinite(converted) or (converted == 0 and value != 0):
        raise SnapshotError(f"{field} 超出网页可显示数字范围。")
    return converted


def expected_period(month: str, now: datetime) -> tuple[date, date | None]:
    start = date.fromisoformat(month + "-01")
    month_end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    last_completed = now.astimezone(BUSINESS_ZONE).date() - timedelta(days=1)
    return start, min(month_end, last_completed) if start <= last_completed else None


def input_records(raw: Any) -> list[dict[str, Any]]:
    records = raw.get("RECORDS") if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise SnapshotError("输入须为 Navicat JSON 行数组，或包含 RECORDS 数组的对象。")
    if any(not isinstance(row, dict) for row in records):
        raise SnapshotError("每条导出记录须为对象。")
    return records


def build_snapshots(raw: Any, expected_months: list[str] | tuple[str, ...], *,
                    now: datetime | None = None, max_age_minutes: int = 60) -> dict[str, dict[str, Any]]:
    """Validate all input before building any monthly payload.

    National amounts remain independent SQL aggregates. Monthly store averages
    use the summed amount divided by the summed positive-amount store-days.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise SnapshotError("当前核验时间必须带时区。")
    if not isinstance(max_age_minutes, int) or isinstance(max_age_minutes, bool) or max_age_minutes <= 0:
        raise SnapshotError("允许的快照年龄必须为正整数分钟。")
    months = list(expected_months)
    if not months or len(set(months)) != len(months) or any(month not in ALLOWED_MONTHS for month in months):
        raise SnapshotError("期望月份须为 2026-09 至 2026-12 的不重复月份。")

    periods = {month: expected_period(month, now) for month in months}
    grouped: dict[str, dict[date, dict[str, dict[str, Any]]]] = defaultdict(lambda: defaultdict(dict))
    queried_at_value: str | None = None
    query_instant: datetime | None = None
    for index, row in enumerate(input_records(raw), start=1):
        if set(row) != INPUT_FIELDS:
            raise SnapshotError(f"第 {index} 行字段不符合六个汇总字段；禁止发布未知附加列。")
        value = row["business_date"]
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise SnapshotError(f"第 {index} 行 business_date 必须为 YYYY-MM-DD。")
        try:
            business_date = date.fromisoformat(value)
        except ValueError:
            raise SnapshotError(f"第 {index} 行 business_date 无效。") from None
        month = value[:7]
        if month not in periods:
            raise SnapshotError(f"第 {index} 行混入非期望月份 {month}。")
        start, end = periods[month]
        if end is None or business_date > end:
            raise SnapshotError(f"第 {index} 行含未来或尚未完成的营业日。")
        if business_date < start:
            raise SnapshotError(f"第 {index} 行日期超出月份范围。")
        region = row["region"]
        if not isinstance(region, str) or region not in REGIONS:
            raise SnapshotError(f"第 {index} 行区域未知。")
        if region in grouped[month][business_date]:
            raise SnapshotError(f"重复营业日/区域：{value} / {region}。")

        observed_time = aware_time(row["queried_at"], "queried_at")
        if query_instant is None:
            queried_at_value, query_instant = row["queried_at"], observed_time
        elif observed_time != query_instant:
            raise SnapshotError("导出混入不同 queried_at；须同一次完整查询结果。")
        gmv = numeric(row["gmv"], "gmv")
        json_number(gmv, "gmv")
        store_days = count(row["store_days"], "store_days")
        source_rows = count(row["source_rows"], "source_rows")
        if store_days > source_rows:
            raise SnapshotError(f"{value} / {region} 的店天超过来源行数。")
        if source_rows == 0 and (gmv != 0 or store_days != 0):
            raise SnapshotError(f"{value} / {region} 的零来源行返回了营业数据。")
        grouped[month][business_date][region] = {
            "gmv": gmv, "storeDays": store_days, "sourceRows": source_rows,
        }

    active_months = [month for month, (_, end) in periods.items() if end is not None]
    if active_months and query_instant is None:
        raise SnapshotError("已完成营业日的月份没有导出记录。")
    if query_instant is not None:
        age = now - query_instant
        if age.total_seconds() < 0:
            raise SnapshotError("queried_at 晚于核验时间，请核实数据库时间和时区。")
        if age > timedelta(minutes=max_age_minutes):
            raise SnapshotError("导出快照超过允许的新鲜度窗口；保留上次成功结果。")

    payloads: dict[str, dict[str, Any]] = {}
    for month in months:
        start, end = periods[month]
        no_data = end is None
        if no_data:
            rows = [{"region": region, "gmv": None, "storeDays": None,
                     "storeAverage": None, "status": "NO_DATA"} for region in REGIONS]
            source_rows_total = None
        else:
            expected_dates = {start + timedelta(days=offset) for offset in range((end - start).days + 1)}
            actual_dates = set(grouped[month])
            if actual_dates != expected_dates:
                missing = sorted(expected_dates - actual_dates)
                example = missing[0].isoformat() if missing else "不在完整周期内的日期"
                raise SnapshotError(f"{month} 日期覆盖不完整，缺少 {example}；须从月初连续覆盖至 {end.isoformat()}。")
            for business_date in sorted(expected_dates):
                missing_regions = set(REGIONS) - set(grouped[month][business_date])
                if missing_regions:
                    raise SnapshotError(f"{business_date.isoformat()} 缺少汇总范围：{'、'.join(sorted(missing_regions))}。")
            rows = []
            source_rows_total = {}
            for region in REGIONS:
                daily = [grouped[month][business_date][region] for business_date in sorted(expected_dates)]
                total_gmv = sum((row["gmv"] for row in daily), Decimal(0))
                total_days = sum(row["storeDays"] for row in daily)
                total_source_rows = sum(row["sourceRows"] for row in daily)
                count(total_days, "累计 store_days")
                count(total_source_rows, "累计 source_rows")
                if total_source_rows == 0:
                    rows.append({"region": region, "gmv": None, "storeDays": None,
                                 "storeAverage": None, "status": "NO_DATA"})
                else:
                    average = json_number(total_gmv / total_days, "storeAverage") if total_days else None
                    rows.append({"region": region, "gmv": json_number(total_gmv, "累计 gmv"),
                                 "storeDays": total_days, "storeAverage": average, "status": "OK"})
                source_rows_total[region] = total_source_rows
        payloads[month] = {
            "month": month, "start": start.isoformat(), "end": end.isoformat() if end else None,
            "fetchedAt": queried_at_value if not no_data else None,
            "source": {
                "label": "Navicat 查询导出快照 · 同步后刷新网页读取",
                "mode": "snapshot", "queryStrategy": "full-period-requery",
                "sourceRefreshedAt": None,
                "completenessCheck": "all-completed-days-and-six-scopes",
                "queryTimestampVerified": False,
                "timestampNote": "保留 SQL queried_at；数据库时钟与 UTC+8 标注尚待现场核实，未以本地转换时间替代。",
                "sourceRefreshNote": "输入未提供上游重算或更新时点；查询时间不能替代源更新时点。",
                "sourceRows": source_rows_total,
            },
            "sourceComplete": False, "dataQualityWarning": QUALITY_WARNING,
            "status": "OK" if any(row["status"] == "OK" for row in rows) else "NO_DATA",
            "rows": rows, "targets": {},
        }
    return payloads


def write_snapshots_atomic(payloads: dict[str, dict[str, Any]], output_dir: Path) -> list[Path]:
    """Stage all serialized files, then atomically replace each monthly file.

    Validation must finish before this function is called. Existing results are
    restored if a caught replacement error occurs. An OS crash across different
    months is not claimed to be a multi-file transaction.
    """
    serialized = {
        month: (json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        for month, payload in payloads.items()
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    previous = {month: (output_dir / f"{month}.json").read_bytes()
                if (output_dir / f"{month}.json").exists() else None for month in serialized}
    replaced: list[str] = []
    with tempfile.TemporaryDirectory(prefix=".snapshot-stage-", dir=output_dir) as staging:
        stage = Path(staging)
        for month, content in serialized.items():
            path = stage / f"{month}.json"
            with path.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        try:
            for month in serialized:
                os.replace(stage / f"{month}.json", output_dir / f"{month}.json")
                replaced.append(month)
        except OSError:
            for month in reversed(replaced):
                target = output_dir / f"{month}.json"
                if previous[month] is None:
                    target.unlink(missing_ok=True)
                else:
                    rollback = stage / f"{month}.rollback"
                    rollback.write_bytes(previous[month])
                    os.replace(rollback, target)
            raise
    return [output_dir / f"{month}.json" for month in serialized]


def load_export(path: Path) -> Any:
    def reject_constant(_value: str) -> None:
        raise SnapshotError("JSON 不允许 NaN 或 Infinity。")
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            return json.load(stream, parse_float=Decimal, parse_constant=reject_constant)
    except (json.JSONDecodeError, UnicodeError):
        raise SnapshotError("输入不是有效的 UTF-8 JSON 导出。") from None


def convert_file(input_path: Path, output_dir: Path, expected_months: list[str] | tuple[str, ...], *,
                 now: datetime | None = None, max_age_minutes: int = 60) -> list[Path]:
    payloads = build_snapshots(load_export(input_path), expected_months,
                               now=now, max_age_minutes=max_age_minutes)
    return write_snapshots_atomic(payloads, output_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证 Navicat 日汇总导出并转换为经营看板月快照；不连接或发布。")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-months", nargs="+", default=list(ALLOWED_MONTHS),
                        help="如 2026-09 2026-10 或 2026-09,2026-10；默认 9–12 月")
    parser.add_argument("--now", help="带时区 ISO 核验时间，仅用于可复现测试；正式排程省略")
    parser.add_argument("--max-age-minutes", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        months = [month for value in args.expected_months for month in value.split(",")]
        now = aware_time(args.now, "--now") if args.now else None
        written = convert_file(args.input, args.output_dir, months,
                               now=now, max_age_minutes=args.max_age_minutes)
    except (SnapshotError, OSError) as error:
        print(f"转换失败：{error}", file=sys.stderr)
        return 1
    print(f"转换完成：{len(written)} 个月快照。尚未上传、部署或完成业务口径核验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
