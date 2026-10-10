"""Synthetic-only verification. No business amounts, database or network access."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from convert_snapshot import (
    ALLOWED_MONTHS, OPERATING_STORES_DEFINITION, OPERATING_STORES_METHOD,
    REGIONS, SnapshotError, build_snapshots, convert_file,
    expected_period, main, write_snapshots_atomic,
)


NOW = datetime.fromisoformat("2026-10-10T12:00:00+08:00")
QUERY_TIME = "2026-10-10T11:59:00+08:00"


def synthetic_rows(months=("2026-09", "2026-10")):
    rows = []
    for month in months:
        start, end = expected_period(month, NOW)
        if end is None:
            continue
        for offset in range((end - start).days + 1):
            for region in REGIONS:
                rows.append({"business_date": (start + timedelta(days=offset)).isoformat(),
                             "region": region, "gmv": "0", "store_days": 0,
                             "queried_at": QUERY_TIME, "source_rows": 0})
    return rows


def set_amount(rows, business_date, region, gmv, days, source_rows=None):
    row = next(row for row in rows if row["business_date"] == business_date and row["region"] == region)
    row.update(gmv=gmv, store_days=days, source_rows=days if source_rows is None else source_rows)
    return row


class ConversionTests(unittest.TestCase):
    def test_independent_national_and_weighted_denominator(self):
        rows = synthetic_rows()
        set_amount(rows, "2026-09-01", "全国", "100.10", 1)
        set_amount(rows, "2026-09-02", "全国", "900.30", 3)
        set_amount(rows, "2026-09-01", "北区", "777.50", 2)
        result = build_snapshots({"RECORDS": rows}, ALLOWED_MONTHS, now=NOW)
        national = result["2026-09"]["rows"][0]
        self.assertEqual(national["gmv"], 1000.4)
        self.assertEqual(national["storeDays"], 4)
        self.assertAlmostEqual(national["storeAverage"], 250.1)
        self.assertEqual(result["2026-09"]["rows"][1]["gmv"], 777.5)
        self.assertNotEqual(national["gmv"], sum(row["gmv"] or 0 for row in result["2026-09"]["rows"][1:]))
        self.assertFalse(result["2026-09"]["sourceComplete"])
        self.assertEqual(result["2026-09"]["fetchedAt"], QUERY_TIME)
        self.assertFalse(result["2026-09"]["source"]["queryTimestampVerified"])
        self.assertEqual(result["2026-09"]["targets"], {})

    def test_no_source_rows_are_no_data_but_real_zero_amount_is_zero(self):
        rows = synthetic_rows()
        blank = build_snapshots(rows, ALLOWED_MONTHS, now=NOW)["2026-09"]
        self.assertEqual(blank["status"], "NO_DATA")
        self.assertTrue(all(row["status"] == "NO_DATA" and row["gmv"] is None
                            and row["storeDays"] is None and row["storeAverage"] is None
                            and row["operatingStores"] is None and row["operatingStoresDate"] is None
                            for row in blank["rows"]))
        self.assertEqual(blank["source"]["sourceRows"], {region: 0 for region in REGIONS})
        # A source record with zero turnover is real data and must stay zero.
        set_amount(rows, "2026-09-01", "全国", "0", 0, source_rows=1)
        present = build_snapshots(rows, ALLOWED_MONTHS, now=NOW)["2026-09"]
        self.assertEqual(present["status"], "OK")
        self.assertEqual(present["rows"][0], {"region": "全国", "gmv": 0.0,
                                             "storeDays": 0, "storeAverage": None, "status": "OK",
                                             "operatingStores": 0, "operatingStoresDate": "2026-09-30"})
        self.assertTrue(all(row["status"] == "NO_DATA" for row in present["rows"][1:]))
        self.assertEqual(present["source"]["sourceRows"]["全国"], 1)

    def test_query_time_does_not_claim_source_refresh_time(self):
        result = build_snapshots(synthetic_rows(), ALLOWED_MONTHS, now=NOW)
        for month in ("2026-09", "2026-10"):
            self.assertEqual(result[month]["fetchedAt"], QUERY_TIME)
            self.assertIsNone(result[month]["source"]["sourceRefreshedAt"])
            self.assertIn("输入未提供", result[month]["source"]["sourceRefreshNote"])
        for month in ("2026-11", "2026-12"):
            self.assertIsNone(result[month]["fetchedAt"])
            self.assertIsNone(result[month]["source"]["sourceRefreshedAt"])

    def test_future_months_have_six_no_data_rows(self):
        result = build_snapshots(synthetic_rows(), ALLOWED_MONTHS, now=NOW)
        self.assertEqual(result["2026-09"]["end"], "2026-09-30")
        self.assertEqual(result["2026-10"]["end"], "2026-10-09")
        for month in ("2026-11", "2026-12"):
            self.assertIsNone(result[month]["end"])
            self.assertIsNone(result[month]["fetchedAt"])
            self.assertEqual(result[month]["status"], "NO_DATA")
            self.assertEqual([row["region"] for row in result[month]["rows"]], list(REGIONS))
            self.assertTrue(all(row["gmv"] is None and row["storeAverage"] is None
                                and row["storeDays"] is None and row["operatingStores"] is None
                                and row["operatingStoresDate"] is None for row in result[month]["rows"]))

    def test_operating_stores_uses_cutoff_day_not_cumulative_or_average(self):
        rows = synthetic_rows()
        set_amount(rows, "2026-10-01", "全国", "100", 1)
        set_amount(rows, "2026-10-02", "全国", "300", 3)
        set_amount(rows, "2026-10-09", "全国", "200", 2)
        result = build_snapshots(rows, ALLOWED_MONTHS, now=NOW)["2026-10"]
        national = result["rows"][0]
        self.assertEqual(national["storeDays"], 6)
        self.assertEqual(national["operatingStores"], 2)
        self.assertEqual(national["operatingStoresDate"], "2026-10-09")
        self.assertEqual(result["source"]["operatingStoresDefinition"], OPERATING_STORES_DEFINITION)
        self.assertEqual(result["source"]["operatingStoresMethod"], OPERATING_STORES_METHOD)

    def test_earlier_backfill_changes_gmv_without_changing_cutoff_store_count(self):
        rows = synthetic_rows()
        set_amount(rows, "2026-10-01", "全国", "100", 1)
        set_amount(rows, "2026-10-09", "全国", "200", 2)
        before = build_snapshots(rows, ALLOWED_MONTHS, now=NOW)["2026-10"]["rows"][0]
        set_amount(rows, "2026-10-01", "全国", "700", 4)
        after = build_snapshots(rows, ALLOWED_MONTHS, now=NOW)["2026-10"]["rows"][0]
        self.assertEqual(before["gmv"], 300)
        self.assertEqual(after["gmv"], 900)
        self.assertNotEqual(before["storeDays"], after["storeDays"])
        self.assertEqual(before["operatingStores"], 2)
        self.assertEqual(after["operatingStores"], 2)
        self.assertEqual(after["operatingStoresDate"], before["operatingStoresDate"])

    def test_cutoff_backfill_updates_store_count_on_full_period_requery(self):
        rows = synthetic_rows()
        set_amount(rows, "2026-10-01", "全国", "100", 1)
        set_amount(rows, "2026-10-09", "全国", "200", 2)
        before = build_snapshots(rows, ALLOWED_MONTHS, now=NOW)["2026-10"]["rows"][0]
        set_amount(rows, "2026-10-09", "全国", "900", 4)
        after = build_snapshots(rows, ALLOWED_MONTHS, now=NOW)["2026-10"]["rows"][0]
        self.assertEqual(before["operatingStores"], 2)
        self.assertEqual(after["operatingStores"], 4)
        self.assertEqual(after["operatingStoresDate"], "2026-10-09")
        self.assertEqual(after["gmv"], 1000)

    def test_zero_source_cutoff_with_earlier_gmv_is_zero_not_missing(self):
        rows = synthetic_rows()
        set_amount(rows, "2026-10-01", "全国", "100", 1)
        result = build_snapshots(rows, ALLOWED_MONTHS, now=NOW)["2026-10"]["rows"][0]
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["gmv"], 100)
        self.assertEqual(result["operatingStores"], 0)
        self.assertEqual(result["operatingStoresDate"], "2026-10-09")

    def test_utc_clock_uses_beijing_completed_day(self):
        utc_now = datetime.fromisoformat("2026-10-09T16:05:00+00:00")
        rows = synthetic_rows()
        for row in rows:
            row["queried_at"] = "2026-10-10T00:04:00+08:00"
        result = build_snapshots(rows, ALLOWED_MONTHS, now=utc_now)
        self.assertEqual(result["2026-10"]["end"], "2026-10-09")

    def test_full_month_backfill_replaces_and_can_withdraw(self):
        rows = synthetic_rows()
        set_amount(rows, "2026-09-08", "全国", "100", 1)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            exported = directory / "export.json"
            output = directory / "out"
            exported.write_text(json.dumps(rows), encoding="utf-8")
            convert_file(exported, output, ALLOWED_MONTHS, now=NOW)
            previous = json.loads((output / "2026-09.json").read_text())
            set_amount(rows, "2026-09-08", "全国", "190", 3)
            exported.write_text(json.dumps(rows), encoding="utf-8")
            convert_file(exported, output, ALLOWED_MONTHS, now=NOW)
            updated = json.loads((output / "2026-09.json").read_text())
            self.assertEqual(previous["rows"][0]["gmv"], 100)
            self.assertEqual(updated["rows"][0]["gmv"], 190)
            self.assertAlmostEqual(updated["rows"][0]["storeAverage"], 190 / 3)
            convert_file(exported, output, ALLOWED_MONTHS, now=NOW)
            self.assertEqual(updated, json.loads((output / "2026-09.json").read_text()))
            set_amount(rows, "2026-09-08", "全国", "80", 2)
            exported.write_text(json.dumps(rows), encoding="utf-8")
            convert_file(exported, output, ALLOWED_MONTHS, now=NOW)
            self.assertEqual(json.loads((output / "2026-09.json").read_text())["rows"][0]["gmv"], 80)

    def test_validation_failure_leaves_success_files_byte_identical(self):
        rows = synthetic_rows()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            exported, output = directory / "export.json", directory / "out"
            exported.write_text(json.dumps(rows), encoding="utf-8")
            convert_file(exported, output, ALLOWED_MONTHS, now=NOW)
            before = {path.name: path.read_bytes() for path in output.glob("*.json")}
            rows.append(deepcopy(rows[0]))
            exported.write_text(json.dumps(rows), encoding="utf-8")
            with self.assertRaises(SnapshotError):
                convert_file(exported, output, ALLOWED_MONTHS, now=NOW)
            self.assertEqual(before, {path.name: path.read_bytes() for path in output.glob("*.json")})

    def test_replacement_error_rolls_back_previously_replaced_months(self):
        initial = build_snapshots(synthetic_rows(), ALLOWED_MONTHS, now=NOW)
        changed_rows = synthetic_rows()
        set_amount(changed_rows, "2026-09-01", "全国", "500", 1)
        updated = build_snapshots(changed_rows, ALLOWED_MONTHS, now=NOW)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            write_snapshots_atomic(initial, directory)
            before = {path.name: path.read_bytes() for path in directory.glob("*.json")}
            from convert_snapshot import os
            original_replace = os.replace
            calls = 0
            def fail_second_replace(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic disk failure")
                return original_replace(source, target)
            with patch("convert_snapshot.os.replace", side_effect=fail_second_replace):
                with self.assertRaises(OSError):
                    write_snapshots_atomic(updated, directory)
            self.assertEqual(before, {path.name: path.read_bytes() for path in directory.glob("*.json")})

    def test_missing_historical_last_day_is_rejected(self):
        rows = [row for row in synthetic_rows() if row["business_date"] != "2026-09-30"]
        with self.assertRaisesRegex(SnapshotError, "2026-09-30"):
            build_snapshots(rows, ALLOWED_MONTHS, now=NOW)

    def test_missing_current_yesterday_is_rejected(self):
        rows = [row for row in synthetic_rows() if row["business_date"] != "2026-10-09"]
        with self.assertRaisesRegex(SnapshotError, "2026-10-09"):
            build_snapshots(rows, ALLOWED_MONTHS, now=NOW)

    def test_missing_scope_duplicate_unknown_and_mixed_month_are_rejected(self):
        original = synthetic_rows()
        cases = []
        cases.append(original[1:])
        duplicate = deepcopy(original)
        duplicate.append(deepcopy(duplicate[0]))
        cases.append(duplicate)
        unknown = deepcopy(original)
        unknown[0]["region"] = "临时区"
        cases.append(unknown)
        mixed = deepcopy(original)
        mixed[0]["business_date"] = "2026-08-31"
        cases.append(mixed)
        for rows in cases:
            with self.subTest(rows=rows[0]):
                with self.assertRaises(SnapshotError):
                    build_snapshots(rows, ALLOWED_MONTHS, now=NOW)

    def test_counts_nonfinite_and_extra_fields_are_rejected(self):
        cases = [("store_days", -1), ("store_days", "1.5"), ("store_days", True),
                 ("store_days", 2**53), ("source_rows", -2), ("gmv", "NaN"),
                 ("gmv", "Infinity"), ("gmv", "1e1000"), ("gmv", "1e-1000"),
                 ("gmv", True), ("gmv", "")]
        for field, value in cases:
            rows = synthetic_rows()
            rows[0][field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(SnapshotError):
                    build_snapshots(rows, ALLOWED_MONTHS, now=NOW)
        rows = synthetic_rows()
        rows[0]["store_name"] = "synthetic extra field"
        with self.assertRaises(SnapshotError):
            build_snapshots(rows, ALLOWED_MONTHS, now=NOW)

    def test_source_count_guard(self):
        rows = synthetic_rows()
        set_amount(rows, "2026-09-01", "全国", "10", 2, 1)
        with self.assertRaisesRegex(SnapshotError, "店天超过"):
            build_snapshots(rows, ALLOWED_MONTHS, now=NOW)

    def test_timestamp_timezone_freshness_and_mixed_queries(self):
        for value in ("2026-10-10T11:59:00", "2026-10-10T10:59:00+08:00", "2026-10-10T12:01:00+08:00"):
            rows = synthetic_rows()
            for row in rows:
                row["queried_at"] = value
            with self.subTest(value=value):
                with self.assertRaises(SnapshotError):
                    build_snapshots(rows, ALLOWED_MONTHS, now=NOW)
        rows = synthetic_rows()
        rows[0]["queried_at"] = "2026-10-10T11:58:00+08:00"
        with self.assertRaisesRegex(SnapshotError, "不同 queried_at"):
            build_snapshots(rows, ALLOWED_MONTHS, now=NOW)
        rows = synthetic_rows()
        for row in rows:
            row["queried_at"] = "2026-10-10T10:59:00+08:00"
        build_snapshots(rows, ALLOWED_MONTHS, now=NOW, max_age_minutes=120)

    def test_today_and_future_days_are_rejected(self):
        for day in ("2026-10-10", "2026-11-01"):
            rows = synthetic_rows()
            rows[0]["business_date"] = day
            with self.subTest(day=day):
                with self.assertRaisesRegex(SnapshotError, "未来或尚未完成"):
                    build_snapshots(rows, ALLOWED_MONTHS, now=NOW)

    def test_cli_accepts_utf8_bom_and_comma_months(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            exported = directory / "navicat.json"
            exported.write_text(json.dumps({"RECORDS": synthetic_rows()}), encoding="utf-8-sig")
            self.assertEqual(main(["--input", str(exported), "--output-dir", str(directory / "out"),
                                   "--expected-months", "2026-09,2026-10,2026-11,2026-12",
                                   "--now", NOW.isoformat()]), 0)
            self.assertEqual(len(list((directory / "out").glob("*.json"))), 4)

    def test_json_nan_and_unsupported_shape_fail_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            exported, output = directory / "navicat.json", directory / "out"
            for text in ('[{"gmv": NaN}]', '{"records": []}'):
                exported.write_text(text, encoding="utf-8")
                with self.subTest(text=text):
                    with self.assertRaises(SnapshotError):
                        convert_file(exported, output, ALLOWED_MONTHS, now=NOW)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
