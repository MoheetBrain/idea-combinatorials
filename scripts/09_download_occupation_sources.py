#!/usr/bin/env python3
"""Discover, download, and validate official occupation and automation sources."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shlex
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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
MANIFEST = ROOT / "data/occupation_source_manifest.csv"
REPORT = ROOT / "reports/occupation_source_download_report.md"
USER_AGENT = "curl/8.7.1 idea-combinatorials-occupation-evidence/1.0 (official-statistics research)"
TIMEOUT = (20, 300)
ATTEMPTS = 3
CHUNK = 1024 * 1024
TRANSIENT = {408, 425, 429, 500, 502, 503, 504}
FIELDS = [
    "source_id", "provider", "dataset", "release_version", "release_date",
    "source_page", "resolved_download", "local_path", "licence", "file_size",
    "sha256", "retrieved_at", "validation_status", "notes",
]


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    provider: str
    dataset: str
    page: str
    target: str
    licence: str
    domains: tuple[str, ...]
    link_text: str = ""
    href_pattern: str = ""
    direct: str = ""
    expected_members: tuple[str, ...] = ()
    release_date: str = ""


SPECS = [
    SourceSpec(
        "onet_database", "O*NET Resource Center", "O*NET production database",
        "https://www.onetcenter.org/database.html",
        "data/raw/occupation/onet_database.csv.zip", "CC BY 4.0",
        ("onetcenter.org",), link_text="Download O*NET", href_pattern=r"db_[0-9_]+_csv\.zip",
        expected_members=("occupation_data.csv", "task_statements.csv", "task_ratings.csv", "tasks_to_dwas.csv"),
    ),
    SourceSpec(
        "onet_soc_crosswalk", "O*NET Resource Center", "O*NET-SOC 2019 to 2018 SOC crosswalk",
        "https://www.onetcenter.org/crosswalks.html",
        "data/raw/occupation/onet_soc2019_to_soc2018.xlsx", "CC BY 4.0",
        ("onetcenter.org",), link_text="O*NET-SOC 2019 occupations to 2018 SOC occupations",
    ),
    SourceSpec(
        "onet_esco_crosswalk", "O*NET Resource Center", "ESCO to O*NET-SOC crosswalk",
        "https://www.onetcenter.org/crosswalks.html",
        "data/raw/occupation/esco_to_onet_soc.xlsx", "CC BY 4.0",
        ("onetcenter.org",), link_text="European Skills, Competences, Qualifications and Occupations",
    ),
    SourceSpec(
        "bls_nem_2024_2034", "U.S. Bureau of Labor Statistics", "National Employment Matrix 2024-34",
        "https://www.bls.gov/emp/data/occupational-data.htm",
        "data/raw/occupation/bls_national_employment_matrix_2024_2034.html.zip", "U.S. federal government work",
        ("bls.gov",), link_text="National Employment Matrix",
        direct="https://data.bls.gov/projections/nationalMatrixHome?ioType=i",
        expected_members=("matrix_index.json",),
        release_date="2025-08-28",
    ),
    SourceSpec(
        "ons_soc2020_volume1", "Office for National Statistics", "SOC 2020 Volume 1 structure and descriptions",
        "https://www.ons.gov.uk/methodology/classificationsandstandards/standardoccupationalclassificationsoc/soc2020/soc2020volume1structureanddescriptionsofunitgroups",
        "data/raw/occupation/ons_soc2020_volume1.xlsx", "Open Government Licence v3.0",
        ("ons.gov.uk",), link_text="soc2020volume1structureanddescriptionofunitgroupsex",
        release_date="2025-12-03",
    ),
    SourceSpec(
        "ons_extended_soc2020", "Office for National Statistics", "Extended SOC 2020 structure and descriptions",
        "https://www.ons.gov.uk/methodology/classificationsandstandards/standardoccupationalclassificationsoc/standardoccupationalclassificationsocextensionproject",
        "data/raw/occupation/ons_extended_soc2020.xlsx", "Open Government Licence v3.0",
        ("ons.gov.uk",), link_text="extendedsoc2020structureanddescriptionsexcel",
        release_date="2025-12-03",
    ),
    SourceSpec(
        "ons_soc2020_coding_index", "Office for National Statistics", "SOC 2020 Volume 2 coding index",
        "https://www.ons.gov.uk/methodology/classificationsandstandards/standardoccupationalclassificationsoc/standardoccupationalclassificationsocextensionproject",
        "data/raw/occupation/ons_soc2020_coding_index.xlsx", "Open Government Licence v3.0",
        ("ons.gov.uk",), link_text="soc2020volume2thecodingindexexcel",
        release_date="2025-12-03",
    ),
    SourceSpec(
        "openai_gpts_are_gpts", "OpenAI", "GPTs are GPTs data and code",
        "https://github.com/openai/GPTs-are-GPTs",
        "data/raw/automation/openai_gpts_are_gpts.zip", "MIT",
        ("github.com", "api.github.com", "codeload.github.com"),
        expected_members=("data/automation_gpt4_human_labels.tsv", "data/full_labelset.tsv", "data/occ_level.csv"),
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


def now() -> str:
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


def request(session: requests.Session, url: str, *, stream: bool = False) -> requests.Response:
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            response = session.get(url, timeout=TIMEOUT, stream=stream, allow_redirects=True)
            if response.status_code in TRANSIENT:
                raise requests.HTTPError(f"transient HTTP {response.status_code}", response=response)
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last = exc
            if attempt + 1 < ATTEMPTS:
                time.sleep(2**attempt)
    raise RuntimeError(f"Request failed after {ATTEMPTS} attempts: {url}: {last}")


def page_links(session: requests.Session, page: str) -> list[tuple[str, str]]:
    response = request(session, page)
    parser = LinkParser()
    parser.feed(response.text)
    return [(urljoin(response.url, href), text) for href, text in parser.links if href]


def discover(session: requests.Session, spec: SourceSpec) -> tuple[str, str, str]:
    if spec.source_id == "openai_gpts_are_gpts":
        api = request(session, "https://api.github.com/repos/openai/GPTs-are-GPTs/commits/main").json()
        revision = str(api["sha"])
        date = str(api["commit"]["committer"]["date"])[:10]
        return f"https://codeload.github.com/openai/GPTs-are-GPTs/zip/{revision}", revision, date
    if spec.source_id == "onet_database":
        page_text = request(session, spec.page).text
        match = re.search(r"O\*NET(?:®)?\s+(\d+\.\d+)\s+Database", page_text, re.I)
        if not match:
            raise RuntimeError("could not identify current O*NET production release")
        version = match.group(1)
        url = f"https://www.onetcenter.org/dl_files/database/db_{version.replace('.', '_')}_csv.zip"
        return url, version, "2026-05" if version == "30.3" else ""
    if spec.direct:
        # BLS blocks automated retrieval of the HTML catalogue but publishes stable,
        # versioned matrix links on that page. The page and direct URL are both kept.
        return spec.direct, "2024-34", spec.release_date
    links = page_links(session, spec.page)
    candidates: list[str] = []
    for url, text in links:
        if spec.link_text.casefold() in (text + " " + url).casefold():
            if not spec.href_pattern or re.search(spec.href_pattern, url, re.I):
                candidates.append(url)
    if not candidates and spec.href_pattern:
        candidates = [url for url, _ in links if re.search(spec.href_pattern, url, re.I)]
    if not candidates:
        raise RuntimeError(f"No official link found for {spec.source_id} on {spec.page}")
    url = candidates[-1]
    version = ""
    date = spec.release_date
    if spec.source_id == "onet_database":
        match = re.search(r"db_([0-9_]+)_(?:csv|excel)", url)
        version = match.group(1).replace("_", ".") if match else "unknown"
        page_text = request(session, spec.page).text
        date_match = re.search(rf"O\*NET\s+{re.escape(version)}\s+Database", page_text)
        if date_match:
            date = "2026-05" if version == "30.3" else ""
    elif spec.source_id.startswith("ons_"):
        version = "SOC 2020"
    elif spec.source_id.startswith("bls_"):
        version = "2024-34"
    elif "crosswalk" in spec.source_id:
        version = "current"
    return url, version, date


def validate_xlsx(path: Path) -> str:
    if not zipfile.is_zipfile(path):
        raise ValueError("not a ZIP-based Office file")
    binary = path.open("rb")
    workbook = load_workbook(binary, read_only=True, data_only=True)
    details = []
    populated = False
    try:
        for sheet in workbook.worksheets:
            cells = 0
            for row in sheet.iter_rows():
                cells += sum(value is not None and str(value).strip() != "" for value in (cell.value for cell in row))
                if cells >= 10:
                    break
            populated |= cells > 0
            details.append(f"{sheet.title}:{sheet.max_row}x{sheet.max_column}")
    finally:
        workbook.close()
        binary.close()
    if not populated:
        raise ValueError("workbook contains no populated cells")
    return "; ".join(details)


def validate_zip(path: Path, expected: tuple[str, ...]) -> str:
    if not zipfile.is_zipfile(path):
        raise ValueError("not a valid ZIP archive")
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"corrupt ZIP member: {bad}")
        names = archive.namelist()
        if not names:
            raise ValueError("empty ZIP archive")
        for suffix in expected:
            if not any(name.endswith(suffix) for name in names):
                raise ValueError(f"expected ZIP member missing: {suffix}")
    if any(name.endswith("matrix_index.json") for name in names) and len(names) < 250:
        raise ValueError(f"incomplete BLS query bundle: only {len(names) - 1} industry pages")
    return f"ZIP readable; {len(names)} members; required members present"


def download_bls_query_bundle(session: requests.Session, part: Path) -> tuple[str, str]:
    """Package exact official data.bls.gov industry result pages when XLSX is edge-blocked."""
    home_url = "https://data.bls.gov/projections/nationalMatrixHome?ioType=i"
    home = request(session, home_url)
    select = re.search(r'<select[^>]+id="selectmatrixIndustry"[^>]*>(.*?)</select>', home.text, re.I | re.S)
    if not select:
        raise RuntimeError("BLS industry selector not found")
    options = []
    for code, title in re.findall(r'<option\s+value="([^"]+)">(.*?)</option>', select.group(1), re.I | re.S):
        options.append((code.strip(), html.unescape(re.sub(r"<[^>]+>", "", title)).strip()))
    options = sorted(set(options))
    if len(options) < 250:
        raise RuntimeError(f"BLS industry directory unexpectedly has only {len(options)} entries")

    def fetch(item: tuple[str, str]) -> tuple[str, str, bytes, str]:
        code, title = item
        url = "https://data.bls.gov/projections/nationalMatrix"
        local_session = requests.Session()
        local_session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
        response = local_session.get(
            url, params={"queryParams": code, "ioType": "i", "_csrf": "projections"},
            timeout=TIMEOUT, allow_redirects=True,
        )
        response.raise_for_status()
        body = response.content
        if b'id="mytable"' not in body.lower() and b"id='mytable'" not in body.lower():
            raise RuntimeError(f"BLS result table missing for industry {code}")
        if b"2024" not in body or b"2034" not in body:
            raise RuntimeError(f"BLS result is not the expected 2024-34 release for {code}")
        return code, title, body, response.url

    results: dict[str, tuple[str, bytes, str]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, item): item for item in options}
        for future in as_completed(futures):
            code, title, body, url = future.result()
            results[code] = (title, body, url)
    fixed = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        index = []
        for code in sorted(results):
            title, body, url = results[code]
            name = f"industry/{re.sub(r'[^A-Za-z0-9_-]', '_', code)}.html"
            info = zipfile.ZipInfo(name, fixed)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, body)
            index.append({"industry_code": code, "industry_title": title, "source_url": url, "member": name})
        info = zipfile.ZipInfo("matrix_index.json", fixed)
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return home_url, f"{len(results)} exact official industry result pages; direct XLSX edge returned HTTP 403"


def validate(path: Path, expected: tuple[str, ...]) -> str:
    if path.stat().st_size <= 0:
        raise ValueError("empty file")
    head = path.read_bytes()[:256].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html", b"{\"error\"", b"{\"message\"")):
        raise ValueError("HTML or JSON error response")
    return validate_zip(path, expected) if expected else validate_xlsx(path)


def load_manifest() -> dict[str, dict[str, str]]:
    if not MANIFEST.exists():
        return {}
    with MANIFEST.open(newline="", encoding="utf-8") as handle:
        return {row["source_id"]: row for row in csv.DictReader(handle)}


def write_manifest(rows: list[dict[str, Any]]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    temp = MANIFEST.with_suffix(".csv.part")
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, MANIFEST)


def write_report(rows: list[dict[str, Any]], command: str) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Occupation Source Download Report", "",
        "| Source | Release | File | Size | Validation | SHA-256 |",
        "|---|---:|---|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['dataset']} | {row['release_version']} | `{row['local_path']}` | "
            f"{row['file_size']} | {row['validation_status']} | `{row['sha256'][:16]}` |"
        )
    lines += ["", f"Official files validated: **{sum(r['validation_status'] == 'passed' for r in rows)}**", "", "Exact command:", "", f"```text\n{command}\n```", ""]
    temp = REPORT.with_suffix(".md.part")
    temp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temp, REPORT)


def run(force: bool, skip_existing: bool) -> int:
    for directory in (ROOT / "data/raw/occupation", ROOT / "data/raw/automation", ROOT / "reports"):
        directory.mkdir(parents=True, exist_ok=True)
    prior = load_manifest()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for spec in SPECS:
        path = ROOT / spec.target
        try:
            url, version, release_date = discover(session, spec)
            if not allowed(url, spec.domains):
                raise RuntimeError(f"discovered URL is outside approved domains: {url}")
            if path.exists() and not force:
                details = validate(path, spec.expected_members)
                previous = prior.get(spec.source_id, {})
                row = {
                    "source_id": spec.source_id, "provider": spec.provider, "dataset": spec.dataset,
                    "release_version": version or previous.get("release_version", ""),
                    "release_date": release_date or previous.get("release_date", ""), "source_page": spec.page,
                    "resolved_download": url, "local_path": spec.target, "licence": spec.licence,
                    "file_size": path.stat().st_size, "sha256": sha256(path),
                    "retrieved_at": previous.get("retrieved_at") or now(), "validation_status": "passed",
                    "notes": f"already valid; {details}",
                }
                rows.append(row)
                continue
            part = path.with_name(path.name + ".part")
            if part.exists() and not force:
                try:
                    details = validate(part, spec.expected_members)
                    os.replace(part, path)
                    rows.append({
                        "source_id": spec.source_id, "provider": spec.provider, "dataset": spec.dataset,
                        "release_version": version, "release_date": release_date, "source_page": spec.page,
                        "resolved_download": url, "local_path": spec.target, "licence": spec.licence,
                        "file_size": path.stat().st_size, "sha256": sha256(path), "retrieved_at": now(),
                        "validation_status": "passed", "notes": f"validated recovered partial download; {details}",
                    })
                    continue
                except Exception:  # noqa: BLE001
                    part.unlink()
            elif part.exists():
                part.unlink()
            download_note = ""
            if spec.source_id == "bls_nem_2024_2034":
                final_url, download_note = download_bls_query_bundle(session, part)
            else:
                response = request(session, url, stream=True)
                final_url = response.url
                if not allowed(final_url, spec.domains):
                    raise RuntimeError(f"redirect outside approved domains: {final_url}")
                length = int(response.headers.get("Content-Length", "0") or 0)
                with part.open("wb") as handle:
                    for chunk in response.iter_content(CHUNK):
                        if chunk:
                            handle.write(chunk)
                if length and part.stat().st_size != length:
                    raise RuntimeError(f"truncated download: expected {length}, got {part.stat().st_size}")
            details = validate(part, spec.expected_members)
            os.replace(part, path)
            rows.append({
                "source_id": spec.source_id, "provider": spec.provider, "dataset": spec.dataset,
                "release_version": version, "release_date": release_date, "source_page": spec.page,
                "resolved_download": final_url, "local_path": spec.target, "licence": spec.licence,
                "file_size": path.stat().st_size, "sha256": sha256(path), "retrieved_at": now(),
                "validation_status": "passed", "notes": f"{details}; {download_note}".rstrip("; "),
            })
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{spec.source_id}: {exc}")
            rows.append({
                "source_id": spec.source_id, "provider": spec.provider, "dataset": spec.dataset,
                "release_version": "", "release_date": spec.release_date, "source_page": spec.page,
                "resolved_download": "", "local_path": spec.target, "licence": spec.licence,
                "file_size": "", "sha256": "", "retrieved_at": now(),
                "validation_status": "failed", "notes": str(exc),
            })
    rows.sort(key=lambda row: row["source_id"])
    write_manifest(rows)
    write_report(rows, " ".join(shlex.quote(part) for part in sys.argv))
    for failure in failures:
        print(f"ERROR {failure}", file=sys.stderr)
    print(f"Validated {len(rows) - len(failures)}/{len(rows)} official source files")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--skip-existing", action="store_true")
    group.add_argument("--force", action="store_true")
    args = parser.parse_args()
    return run(args.force, args.skip_existing)


if __name__ == "__main__":
    raise SystemExit(main())
