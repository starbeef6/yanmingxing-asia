#!/usr/bin/env python3
"""Append source-backed operating-store goals from the first XLSX worksheet.

Keep decimal source values and the existing GMV / store-average goals. Display
rounding belongs to the UI; national goals are never replaced by regional sums.
"""
import argparse
import hashlib
import json
import posixpath
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
REGIONS = ["北区", "西区", "南区", "四川地区", "江苏地区", "全国"]
SOURCE_REGIONS = ["北区", "西区", "南区", "四川", "江苏", "全国"]
TIERS = {"保底": "E", "平衡": "K", "冲刺": "Q"}


def extract(source, targets):
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if targets["source"]["sha256"] != source_sha:
        raise ValueError("Source workbook differs from the existing GMV target version")
    with ZipFile(source) as archive:
        sheet = ET.fromstring(archive.read("xl/workbook.xml")).find("s:sheets", NS)[0]
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(r.attrib["Target"] for r in rels if r.attrib["Id"] == sheet.attrib[RID])
        sheet_path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        if sheet.attrib["name"] != targets["source"]["sheet"]:
            raise ValueError("The first worksheet does not match the target source")
        cells = {c.attrib["r"]: c for c in ET.fromstring(archive.read(sheet_path)).findall(".//s:sheetData/s:row/s:c", NS)}
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(e.itertext()) for e in ET.fromstring(archive.read("xl/sharedStrings.xml"))]

        def text(cell):
            e = cells[cell]
            v = e.find("s:v", NS)
            if e.attrib.get("t") == "s":
                return strings[int(v.text)]
            if e.attrib.get("t") == "inlineStr":
                return "".join(t.text or "" for t in e.findall(".//s:t", NS))
            return v.text if v is not None else None

        records = []
        reconciliation = []
        for month, title_row in [(9, 13), (10, 23), (11, 33), (12, 43)]:
            month_key = f"2026-{month:02d}"
            if text(f"B{title_row}") != f"{month}月份目标值":
                raise ValueError("Unexpected monthly target block")
            for tier, col in TIERS.items():
                if text(f"{col}{title_row + 2}") != "在营门店数":
                    raise ValueError("Unexpected store-count target header")
                group = []
                for offset, (region, source_region) in enumerate(zip(REGIONS, SOURCE_REGIONS)):
                    row = title_row + 3 + offset
                    if text(f"B{row}") != source_region:
                        raise ValueError("Unexpected regional target row")
                    coordinate = f"{col}{row}"
                    cell = cells[coordinate]
                    if cell.find("s:f", NS) is not None or cell.attrib.get("t") not in (None, "n"):
                        raise ValueError("An operating-store goal is not a cached numeric source value")
                    raw = text(coordinate)
                    value = Decimal(raw)
                    if not value.is_finite() or value < 0:
                        raise ValueError("Invalid operating-store target")
                    numeric = int(value) if value == value.to_integral_value() else float(value)
                    targets["months"][month_key][tier][region]["operatingStores"] = numeric
                    record = {"month": month_key, "tier": tier, "region": region, "cell": coordinate, "cachedRaw": raw, "operatingStores": numeric}
                    records.append(record)
                    group.append(value)
                reconciliation.append({"month": month_key, "tier": tier, "nationalMinusFiveRegionsExact": str(group[-1] - sum(group[:-1]))})
    if len(records) != 72 or hashlib.sha256(source.read_bytes()).hexdigest() != source_sha:
        raise ValueError("Incomplete target extraction or source file changed")
    targets["source"]["units"]["operatingStores"] = "家"
    targets["source"]["operatingStores"] = {"columns": TIERS, "displayFractionDigits": 0, "definition": "原表在营门店数目标；月末状态、月均及有营业额口径未注明", "roundingNote": "保留源小数，按原表整数格式显示，不补全国与五区差额"}
    return {"sourceSha256": source_sha, "sheet": sheet.attrib["name"], "records": records, "reconciliation": reconciliation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    targets = json.loads(args.targets.read_text(encoding="utf-8"))
    audit = extract(args.source, targets)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.targets.write_text(json.dumps(targets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Added {len(audit['records'])} source-backed operating-store targets")


if __name__ == "__main__":
    main()
