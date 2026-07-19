#!/usr/bin/env python3
"""Download and validate official financial, labour, and risk source files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

try:
    import requests
    from openpyxl import load_workbook
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit(f"Missing dependency {exc.name!r}; install requirements.txt") from exc


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/enrichment_source_manifest.csv"
REPORT = ROOT / "reports/enrichment_download_report.md"
USER_AGENT = (
    "curl/8.7.1 idea-combinatorials-enrichment-downloader/1.0 "
    "(official-statistics research; non-commercial reproducible pipeline)"
)
TIMEOUT = (20, 240)
ATTEMPTS = 3
CHUNK = 1024 * 1024
TRANSIENT = {408, 425, 429, 500, 502, 503, 504}

MANIFEST_FIELDS = [
    "source_id", "dataset_name", "provider", "source_page_url",
    "requested_download_url", "resolved_download_url", "local_path",
    "file_format", "file_size_bytes", "sha256", "retrieved_at_utc",
    "http_status", "validation_status", "validation_details", "source_year",
    "release_date", "geography", "monetary_units", "licence", "access_status",
    "notes",
]


@dataclass(frozen=True)
class Source:
    source_id: str
    dataset_name: str
    provider: str
    page_url: str
    requested_url: str
    local_path: str
    file_format: str
    source_year: str
    release_date: str
    geography: str
    monetary_units: str
    licence: str
    allowed_domains: tuple[str, ...]
    discover_contains: str = ""
    discover_text: str = ""


SOURCES = [
    Source(
        "abs_2024_sections", "Annual Business Survey 2024: Sections A to S",
        "Office for National Statistics",
        "https://www.ons.gov.uk/businessindustryandtrade/business/businessservices/datasets/uknonfinancialbusinesseconomyannualbusinesssurveysectionsas/current",
        "https://www.ons.gov.uk/file?uri=%2Fbusinessindustryandtrade%2Fbusiness%2Fbusinessservices%2Fdatasets%2Fuknonfinancialbusinesseconomyannualbusinesssurveysectionsas%2Fcurrent%2Fabssectionsas.xlsx",
        "data/raw/financial/abs_2024_sections.xlsx", "xlsx", "2024", "2026-05-26",
        "United Kingdom", "GBP millions unless workbook states otherwise",
        "Open Government Licence v3.0", ("ons.gov.uk",), "abssectionsas.xlsx",
    ),
    Source(
        "abs_2024_employment_size_band", "Annual Business Survey 2024: employment size bands",
        "Office for National Statistics",
        "https://www.ons.gov.uk/businessindustryandtrade/business/businessservices/datasets/uknonfinancialbusinesseconomyannualbusinesssurveyemploymentsizeband/current",
        "https://www.ons.gov.uk/file?uri=%2Fbusinessindustryandtrade%2Fbusiness%2Fbusinessservices%2Fdatasets%2Fuknonfinancialbusinesseconomyannualbusinesssurveyemploymentsizeband%2Fcurrent%2Fabssizeband.xlsx",
        "data/raw/financial/abs_2024_employment_size_band.xlsx", "xlsx", "2024", "2026-05-26",
        "United Kingdom", "GBP millions unless workbook states otherwise",
        "Open Government Licence v3.0", ("ons.gov.uk",), "abssizeband.xlsx",
    ),
    Source(
        "abs_2024_quality", "Annual Business Survey 2024: UK quality measures",
        "Office for National Statistics",
        "https://www.ons.gov.uk/businessindustryandtrade/business/businessservices/datasets/uknonfinancialbusinesseconomyannualbusinesssurveyqualitymeasures/current",
        "https://www.ons.gov.uk/file?uri=%2Fbusinessindustryandtrade%2Fbusiness%2Fbusinessservices%2Fdatasets%2Fuknonfinancialbusinesseconomyannualbusinesssurveyqualitymeasures%2Fcurrent%2Fabsnationalqualitymeasures.xlsx",
        "data/raw/financial/abs_2024_quality_measures.xlsx", "xlsx", "2024", "2026-05-26",
        "United Kingdom", "Workbook-specific quality measures",
        "Open Government Licence v3.0", ("ons.gov.uk",), "absnationalqualitymeasures.xlsx",
    ),
    Source(
        "abs_2024_regional", "Annual Business Survey 2024: regional Sections A to S",
        "Office for National Statistics",
        "https://www.ons.gov.uk/businessindustryandtrade/business/businessservices/datasets/uknonfinancialbusinesseconomyannualbusinesssurveyregionalresultssectionsas/current",
        "https://www.ons.gov.uk/file?uri=%2Fbusinessindustryandtrade%2Fbusiness%2Fbusinessservices%2Fdatasets%2Fuknonfinancialbusinesseconomyannualbusinesssurveyregionalresultssectionsas%2Fcurrent%2Fabsregionalsectionas2024.xlsx",
        "data/raw/financial/abs_2024_regional_sections.xlsx", "xlsx", "2024", "2026-06-04",
        "United Kingdom regions and countries", "GBP millions unless workbook states otherwise",
        "Open Government Licence v3.0", ("ons.gov.uk",), "absregionalsectionas",
    ),
    Source(
        "abs_2024_regional_quality", "Annual Business Survey 2024: regional quality measures",
        "Office for National Statistics",
        "https://www.ons.gov.uk/businessindustryandtrade/business/businessservices/datasets/uknonfinancialbusinesseconomyannualbusinesssurveyregionalresultsqualitymeasures",
        "https://www.ons.gov.uk/file?uri=%2Fbusinessindustryandtrade%2Fbusiness%2Fbusinessservices%2Fdatasets%2Fuknonfinancialbusinesseconomyannualbusinesssurveyregionalresultsqualitymeasures%2F2024%2Fabsregionalqualitymeasures2024.xlsx",
        "data/raw/financial/abs_2024_regional_quality.xlsx", "xlsx", "2024", "2026-06-04",
        "United Kingdom regions and countries", "Workbook-specific quality measures",
        "Open Government Licence v3.0", ("ons.gov.uk",), "absregionalqualitymeasures2024.xlsx",
    ),
    Source(
        "uk_business_2025", "UK Business: activity, size and location 2025",
        "Office for National Statistics",
        "https://www.ons.gov.uk/businessindustryandtrade/business/activitysizeandlocation/datasets/ukbusinessactivitysizeandlocation",
        "https://www.ons.gov.uk/file?uri=%2Fbusinessindustryandtrade%2Fbusiness%2Factivitysizeandlocation%2Fdatasets%2Fukbusinessactivitysizeandlocation%2F2025%2Fukbusinessworkbook2025new.xlsx",
        "data/raw/financial/uk_business_2025.xlsx", "xlsx", "2025", "2025-09-24",
        "United Kingdom", "Counts; turnover bands in GBP",
        "Open Government Licence v3.0", ("ons.gov.uk",), "ukbusinessworkbook2025",
    ),
    Source(
        "bres_2024_provisional", "BRES 2024 provisional: industry table 2",
        "Office for National Statistics",
        "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/datasets/industry235digitsicbusinessregisterandemploymentsurveybrestable2",
        "https://www.ons.gov.uk/file?uri=%2Femploymentandlabourmarket%2Fpeopleinwork%2Femploymentandemployeetypes%2Fdatasets%2Findustry235digitsicbusinessregisterandemploymentsurveybrestable2%2F2024provisional%2Ftable22024p.xlsx",
        "data/raw/labour/bres_2024_provisional.xlsx", "xlsx", "2024", "2025-10-28",
        "Great Britain", "Employment counts",
        "Open Government Licence v3.0", ("ons.gov.uk",), "table22024p.xlsx",
    ),
    Source(
        "bres_2024_cv", "BRES 2024 provisional: coefficients of variation",
        "Office for National Statistics",
        "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/datasets/industry235digitsicbusinessregisterandemploymentsurveybrestable2",
        "https://www.ons.gov.uk/file?uri=%2Femploymentandlabourmarket%2Fpeopleinwork%2Femploymentandemployeetypes%2Fdatasets%2Findustry235digitsicbusinessregisterandemploymentsurveybrestable2%2F2024provisional%2Fcvtable22024p.xlsx",
        "data/raw/labour/bres_2024_cv.xlsx", "xlsx", "2024", "2025-10-28",
        "Great Britain", "Coefficients of variation",
        "Open Government Licence v3.0", ("ons.gov.uk",), "cvtable22024p.xlsx",
    ),
    Source(
        "ashe_2025_table29", "ASHE 2025 provisional: industry by occupation, table 29",
        "Office for National Statistics",
        "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/earningsandworkinghours/datasets/earningsandhoursworkedbyindustryandoccupationashetable29",
        "https://www.ons.gov.uk/file?uri=%2Femploymentandlabourmarket%2Fpeopleinwork%2Fearningsandworkinghours%2Fdatasets%2Fearningsandhoursworkedbyindustryandoccupationashetable29%2F2025provisional%2Fashetable292025provisional.zip",
        "data/raw/labour/ashe_table29_2025_provisional.zip", "zip", "2025", "2025-10-23",
        "United Kingdom", "GBP and paid hours as labelled",
        "Open Government Licence v3.0", ("ons.gov.uk",), "ashetable292025provisional.zip",
    ),
    Source(
        "ashe_2025_table5", "ASHE 2025 provisional: region by industry, table 5",
        "Office for National Statistics",
        "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/earningsandworkinghours/datasets/regionbyindustry2digitsicashetable5",
        "https://www.ons.gov.uk/file?uri=%2Femploymentandlabourmarket%2Fpeopleinwork%2Fearningsandworkinghours%2Fdatasets%2Fregionbyindustry2digitsicashetable5%2F2025provisional%2Fashetable52025provisional.zip",
        "data/raw/labour/ashe_table5_2025_provisional.zip", "zip", "2025", "2025-10-23",
        "United Kingdom regions", "GBP and paid hours as labelled",
        "Open Government Licence v3.0", ("ons.gov.uk",), "ashetable52025provisional.zip",
    ),
    Source(
        "business_demography_2024", "Business demography 2024 reference tables",
        "Office for National Statistics",
        "https://www.ons.gov.uk/businessindustryandtrade/business/activitysizeandlocation/datasets/businessdemographyreferencetable",
        "https://www.ons.gov.uk/file?uri=%2Fbusinessindustryandtrade%2Fbusiness%2Factivitysizeandlocation%2Fdatasets%2Fbusinessdemographyreferencetable%2Fcurrent%2Fbusinessdemographyexceltables2024.xlsx",
        "data/raw/risk/business_demography_2024.xlsx", "xlsx", "2024", "2026-01-15",
        "United Kingdom", "Counts and percentages",
        "Open Government Licence v3.0", ("ons.gov.uk",), "businessdemographyexceltables2024.xlsx",
    ),
    Source(
        "business_insolvency_demography_2015_2025", "Business insolvency demography 2015 to 2025",
        "Insolvency Service",
        "https://www.gov.uk/government/statistics/business-insolvency-demography-2015-to-2025",
        "https://assets.publishing.service.gov.uk/media/6a35475600ff27f06e3efb37/Data_Tables_in_Excel__xlsx__Format_-_Business_Insolvency_Demography_2015_to_2025.xlsx",
        "data/raw/risk/business_insolvency_demography_2015_2025.xlsx", "xlsx", "2015-2025", "2026-06-19",
        "United Kingdom", "Counts and rates",
        "Open Government Licence v3.0", ("publishing.service.gov.uk", "gov.uk"), "business_insolvency_demography", "Data Tables in Excel",
    ),
    Source(
        "company_insolvencies_2026_06", "Company insolvency statistics: June 2026",
        "Insolvency Service",
        "https://www.gov.uk/government/statistics/company-insolvencies-june-2026",
        "https://assets.publishing.service.gov.uk/media/6a57a5272cbe5d1c179a65bb/Data_Tables_in_Excel__xlsx__Format_-_Company_Insolvency_Statistics_June_2026.xlsx",
        "data/raw/risk/company_insolvencies_2026_06.xlsx", "xlsx", "2026", "2026-07-17",
        "England and Wales; Scotland; Northern Ireland", "Counts and rates",
        "Open Government Licence v3.0", ("publishing.service.gov.uk", "gov.uk"), "company_insolvency_statistics", "Data Tables in Excel",
    ),
    Source(
        "company_insolvencies_industry_2026_06", "Company insolvency statistics: June 2026 industry tables",
        "Insolvency Service",
        "https://www.gov.uk/government/statistics/company-insolvencies-june-2026",
        "https://assets.publishing.service.gov.uk/media/6a58e5a15ca06bf11ccb4335/Industry_Tables_in_Excel__xlsx__Format_-_Company_Insolvency_Statistics_June_2026.xlsx",
        "data/raw/risk/company_insolvencies_industry_2026_06.xlsx", "xlsx", "2026", "2026-07-17",
        "England and Wales", "Counts and rates",
        "Open Government Licence v3.0", ("publishing.service.gov.uk", "gov.uk"), "industry_tables", "Industry Tables in Excel",
    ),
]


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None
            self._text = []


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def allowed(url: str, domains: tuple[str, ...]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme == "https" and any(host == d or host.endswith("." + d) for d in domains)


def discover(session: requests.Session, spec: Source) -> tuple[str, str]:
    """Resolve the current official link while retaining the prompt-era requested URL."""
    response = session.get(spec.page_url, timeout=TIMEOUT)
    response.raise_for_status()
    parser = LinkParser()
    parser.feed(response.text)
    candidates: list[str] = []
    for href, text in parser.links:
        url = urljoin(spec.page_url, href)
        lowered = url.casefold()
        if not allowed(url, spec.allowed_domains):
            continue
        if spec.discover_text and spec.discover_text.casefold() not in text.casefold():
            continue
        if spec.discover_contains and spec.discover_contains.casefold() not in lowered:
            continue
        if spec.file_format == "xlsx" and (".xlsx" not in lowered and "/file?uri=" not in lowered):
            continue
        if spec.file_format == "zip" and (".zip" not in lowered and "/file?uri=" not in lowered):
            continue
        candidates.append(url)
    if candidates:
        return candidates[0], "Discovered from the official source page."
    return spec.requested_url, "Official-page discovery found no uniquely matching link; used the recorded official requested URL."


def reject_error(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("empty file")
    with path.open("rb") as handle:
        prefix = handle.read(4096).lstrip().lower()
    if prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        raise ValueError("HTML response saved instead of data")
    if prefix.startswith((b"{", b"[")):
        try:
            json.loads(prefix.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        raise ValueError("JSON response saved instead of data")


def validate_xlsx(path: Path) -> dict[str, Any]:
    reject_error(path)
    if not zipfile.is_zipfile(path):
        raise ValueError("not a valid ZIP-based Office workbook")
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
    if corrupt:
        raise ValueError(f"corrupt workbook member: {corrupt}")
    with path.open("rb") as handle:
        workbook = load_workbook(handle, read_only=True, data_only=True)
        try:
            sheets = []
            populated = False
            for sheet in workbook.worksheets:
                count = 0
                for row in sheet.iter_rows():
                    count += sum(cell.value not in (None, "") for cell in row)
                populated |= count > 0
                sheets.append({"name": sheet.title, "rows": sheet.max_row, "columns": sheet.max_column, "nonempty_cells": count})
            if not sheets or not populated:
                raise ValueError("workbook has no populated worksheet")
            return {"validator": "zipfile+openpyxl", "worksheets": sheets}
        finally:
            workbook.close()


def validate_zip(path: Path) -> dict[str, Any]:
    reject_error(path)
    if not zipfile.is_zipfile(path):
        raise ValueError("not a valid ZIP archive")
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
        members = [info for info in archive.infolist() if not info.is_dir()]
        data_members = [m.filename for m in members if m.filename.casefold().endswith((".xlsx", ".xls", ".csv"))]
    if corrupt:
        raise ValueError(f"corrupt ZIP member: {corrupt}")
    if not data_members:
        raise ValueError("ZIP contains no Excel or CSV data file")
    return {"validator": "zipfile", "members": len(members), "data_members": data_members}


def validate(spec: Source, path: Path) -> dict[str, Any]:
    return validate_xlsx(path) if spec.file_format == "xlsx" else validate_zip(path)


def download(session: requests.Session, url: str, spec: Source, part: Path) -> tuple[str, int]:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        part.unlink(missing_ok=True)
        try:
            with session.get(url, stream=True, allow_redirects=True, timeout=TIMEOUT) as response:
                if response.status_code >= 400:
                    error = requests.HTTPError(f"HTTP {response.status_code}")
                    error.response = response
                    raise error
                if not allowed(response.url, spec.allowed_domains):
                    raise ValueError(f"redirected to unrelated domain: {response.url}")
                content_type = response.headers.get("Content-Type", "").casefold()
                if "text/html" in content_type or "application/json" in content_type:
                    raise ValueError(f"unexpected content type {content_type!r}")
                written = 0
                with part.open("wb") as handle:
                    for chunk in response.iter_content(CHUNK):
                        if chunk:
                            handle.write(chunk)
                            written += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                expected = response.headers.get("Content-Length")
                if expected and response.headers.get("Content-Encoding", "identity") in ("", "identity") and written != int(expected):
                    raise IOError(f"truncated response: expected {expected}, received {written}")
                reject_error(part)
                return response.url, response.status_code
        except Exception as exc:  # network library exposes several retryable subclasses
            last = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = isinstance(exc, (requests.Timeout, requests.ConnectionError, IOError)) or status in TRANSIENT
            part.unlink(missing_ok=True)
            if attempt == ATTEMPTS or not retryable:
                break
            retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
            try:
                delay = max(5, int(retry_after)) if retry_after else 5 * (2 ** (attempt - 1))
            except ValueError:
                delay = 5 * (2 ** (attempt - 1))
            time.sleep(min(delay, 30))
    raise RuntimeError(f"download failed: {last}") from last


def previous_manifest() -> dict[str, dict[str, str]]:
    if not MANIFEST.exists():
        return {}
    with MANIFEST.open(newline="", encoding="utf-8") as handle:
        return {row["source_id"]: row for row in csv.DictReader(handle)}


def base_row(spec: Source) -> dict[str, str]:
    row = {field: "" for field in MANIFEST_FIELDS}
    row.update({
        "source_id": spec.source_id, "dataset_name": spec.dataset_name,
        "provider": spec.provider, "source_page_url": spec.page_url,
        "requested_download_url": spec.requested_url, "local_path": spec.local_path,
        "file_format": spec.file_format, "source_year": spec.source_year,
        "release_date": spec.release_date, "geography": spec.geography,
        "monetary_units": spec.monetary_units, "licence": spec.licence,
    })
    return row


def process(session: requests.Session, spec: Source, previous: dict[str, dict[str, str]], force: bool) -> dict[str, str]:
    row = base_row(spec)
    path = ROOT / spec.local_path
    path.parent.mkdir(parents=True, exist_ok=True)
    prior = previous.get(spec.source_id, {})
    if path.exists() and not force:
        try:
            details = validate(spec, path)
        except Exception as exc:
            row.update({"access_status": "failed", "validation_status": "failed", "validation_details": str(exc), "notes": "Existing file invalid; use --force to replace it."})
            return row
        row.update({
            "requested_download_url": prior.get("requested_download_url", spec.requested_url),
            "resolved_download_url": prior.get("resolved_download_url", spec.requested_url),
            "file_size_bytes": str(path.stat().st_size), "sha256": sha256(path),
            "retrieved_at_utc": prior.get("retrieved_at_utc") or datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            "http_status": prior.get("http_status", ""), "validation_status": "passed",
            "validation_details": json.dumps(details, sort_keys=True), "access_status": "already_valid",
            "notes": "Existing file revalidated; no page request or download was performed.",
        })
        return row
    try:
        discovered, discovery_note = discover(session, spec)
    except Exception as exc:
        discovered, discovery_note = spec.requested_url, f"Official-page discovery failed ({exc}); used recorded official URL."
    row["requested_download_url"] = discovered
    part = path.with_name(path.name + ".part")
    try:
        resolved, status = download(session, discovered, spec, part)
        details = validate(spec, part)
        os.replace(part, path)
        row.update({
            "resolved_download_url": resolved, "file_size_bytes": str(path.stat().st_size),
            "sha256": sha256(path), "retrieved_at_utc": utc_now(), "http_status": str(status),
            "validation_status": "passed", "validation_details": json.dumps(details, sort_keys=True),
            "access_status": "downloaded", "notes": discovery_note + " Atomic replacement followed validation.",
        })
    except Exception as exc:
        part.unlink(missing_ok=True)
        row.update({"access_status": "failed", "validation_status": "failed", "validation_details": str(exc), "notes": discovery_note})
    return row


def write_manifest(rows: list[dict[str, str]]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    temp = MANIFEST.with_suffix(".csv.part")
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, MANIFEST)


def write_report(rows: list[dict[str, str]], command: str) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    total = sum(int(row["file_size_bytes"] or 0) for row in rows if row["validation_status"] == "passed")
    lines = [
        "# Enrichment source download report", "",
        f"Generated: `{utc_now()}`", "",
        "| Source | Year | Status | Size | Validation | Saved path |",
        "|---|---:|---|---:|---|---|",
    ]
    for row in rows:
        lines.append(f"| {row['dataset_name']} | {row['source_year']} | {row['access_status']} | {int(row['file_size_bytes'] or 0):,} bytes | {row['validation_status']} | `{row['local_path']}` |")
    failures = [row for row in rows if row["access_status"] == "failed"]
    lines += [
        "", "## Summary", "",
        f"- Sources validated: {len(rows) - len(failures)} of {len(rows)}",
        f"- Total validated disk usage: {total:,} bytes",
        f"- Failures: {len(failures)}",
        f"- Manifest: `{MANIFEST.relative_to(ROOT)}`",
        f"- Exact command: `{command}`",
        "", "## Failures", "",
    ]
    if failures:
        for row in failures:
            lines.append(f"- **{row['dataset_name']}**: {row['validation_details']}")
    else:
        lines.append("None.")
    lines += [
        "", "## Scope notes", "",
        "- Foreign calibration sources were not downloaded: the required UK sources are machine-readable and are preferred for this phase.",
        "- ABS monetary units are not normalised during acquisition; workbook-specific units are interpreted and recorded by the parser.",
        "- Reporting units, enterprises, and local units remain distinct populations.",
    ]
    temp = REPORT.with_suffix(".md.part")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temp, REPORT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--skip-existing", action="store_true", help="Revalidate valid files without downloading them again (default behavior).")
    group.add_argument("--force", action="store_true", help="Redownload even valid existing files.")
    args = parser.parse_args(argv)
    for directory in ("data/raw/financial", "data/raw/labour", "data/raw/risk", "reports"):
        (ROOT / directory).mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    prior = previous_manifest()
    rows = []
    for spec in SOURCES:
        print(f"[{spec.source_id}]")
        row = process(session, spec, prior, args.force)
        print(f"  {row['access_status']}: {row['validation_status']}")
        rows.append(row)
    rows.sort(key=lambda row: row["source_id"])
    write_manifest(rows)
    command = shlex.join([sys.executable, str(Path(__file__).relative_to(ROOT)), *(argv if argv is not None else sys.argv[1:])])
    write_report(rows, command)
    failures = [row for row in rows if row["access_status"] == "failed"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
