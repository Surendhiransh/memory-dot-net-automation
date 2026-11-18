from __future__ import annotations

import argparse
import ast
import csv
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory


MAIN_CSV = Path(r"C:\Users\bitco\Downloads\crucial\ASRock_crucial_db_import.csv")
AMD_CSV = Path(r"C:\Users\bitco\Documents\cisco_pytest\Cruical\09022026_amd_db_import.csv")
INTEL_CSV = Path(r"C:\Users\bitco\Documents\cisco_pytest\Cruical\12022026_intel_db_import.csv")

STATUS_MATCH = "Match"
STATUS_MISSING = "Missing"
STATUS_MISMATCH = "Mismatch"
STATUS_DUPLICATE = "Duplicate"
STATUS_NO_FAMILY = "No family is mapped in main CSV"

DETAIL_COLUMNS = [
    "server_description",
    "processor",
    "status",
    "processor_max_memory_speed",
    "maximum_memory_speed",
    "reason",
    "match_count",
    "missing_count",
    "mismatch_count",
    "duplicate_processor_count",
    "duplicate_processor_data",
    "duplicate_processor_max_memory_speed",
    "missing_processor_max_memory_speed",
    "mismatch_processor_max_memory_speed",
]


def fix_mojibake(value: object) -> str:
    text = "" if value is None else str(value)
    if "Â" in text or "â" in text:
        try:
            return text.encode("cp1252").decode("utf-8")
        except UnicodeError:
            replacements = {
                "Â®": "\u00ae",
                "â„¢": "\u2122",
                "â€™": "'",
                "â€“": "-",
                "â€”": "-",
                "â€‹": "",
            }
            for old, new in replacements.items():
                text = text.replace(old, new)
    return text


def clean_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", fix_mojibake(value))
    return re.sub(r"\s+", " ", text).strip()


def product_key(value: object) -> str:
    text = clean_text(value)
    text = "".join(ch for ch in text if ord(ch) not in {0x00AE, 0x2122})
    return clean_text(text).casefold()


def speed_key(value: object) -> str:
    return clean_text(value).casefold()


def parse_list_cell(value: object) -> list[str]:
    text = "" if value is None else str(value).strip()
    if not text or text == "[]":
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return [clean_text(text)]
        if isinstance(parsed, list):
            return [clean_text(item) for item in parsed if clean_text(item)]
    return [clean_text(text)]


def parse_processor_speed_entry(entry: str) -> tuple[str, list[str]]:
    entry = clean_text(entry)
    speeds = [clean_text(speed) for speed in re.findall(r"\(([^()]*)\)", entry) if clean_text(speed)]
    if not speeds:
        return entry, []

    processor = re.sub(r"\s*(?:\([^()]*\)\s*)+$", "", entry)
    return clean_text(processor), speeds


def read_csv_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def build_lookup(amd_csv: Path, intel_csv: Path) -> dict[str, list[dict[str, str]]]:
    lookup: dict[str, list[dict[str, str]]] = defaultdict(list)
    for source, path in (("AMD", amd_csv), ("Intel", intel_csv)):
        for row_number, row in enumerate(read_csv_rows(path), start=2):
            product_name = clean_text(row.get("product_name", ""))
            if not product_name:
                continue

            lookup[product_key(product_name)].append(
                {
                    "source": source,
                    "csv_row": str(row_number),
                    "product_name": product_name,
                    "maximum_memory_speed": clean_text(row.get("maximum_memory_speed", "")),
                    "product_family": clean_text(row.get("product_family", "")),
                    "product_line": clean_text(
                        row.get("product_line", "") or row.get("processor_series", "")
                    ),
                }
            )
    return lookup


def summarize_lookup_hits(hits: list[dict[str, str]]) -> dict[str, str]:
    nonblank_speed_keys = sorted(
        {speed_key(hit["maximum_memory_speed"]) for hit in hits if speed_key(hit["maximum_memory_speed"])}
    )
    nonblank_speeds = sorted(
        {hit["maximum_memory_speed"] for hit in hits if clean_text(hit["maximum_memory_speed"])}
    )

    return {
        "lookup_source": "; ".join(sorted({hit["source"] for hit in hits})),
        "lookup_csv_rows": "; ".join(hit["csv_row"] for hit in hits),
        "lookup_product_name": "; ".join(sorted({hit["product_name"] for hit in hits})),
        "lookup_maximum_memory_speed": "; ".join(nonblank_speeds),
        "lookup_product_family": "; ".join(sorted({hit["product_family"] for hit in hits if hit["product_family"]})),
        "lookup_product_line": "; ".join(sorted({hit["product_line"] for hit in hits if hit["product_line"]})),
        "lookup_duplicate_count": str(len(hits)),
        "lookup_distinct_speed_count": str(len(nonblank_speed_keys)),
        "_single_speed_key": nonblank_speed_keys[0] if len(nonblank_speed_keys) == 1 else "",
        "_has_ambiguous_speeds": "yes" if len(nonblank_speed_keys) > 1 else "",
    }


def status_for_processor(
    processor: str,
    family: str,
    actual_speed: str,
    hits: list[dict[str, str]],
    is_duplicate_processor: bool = False,
) -> tuple[str, str, dict[str, str]]:
    if is_duplicate_processor:
        return (
            STATUS_DUPLICATE,
            "same processor has multiple processor_max_memory_speed values in main CSV",
            {},
        )

    if not hits:
        return STATUS_MISSING, "processor not found in AMD or Intel product_name", {}

    lookup_summary = summarize_lookup_hits(hits)
    lookup_speed_keys = {
        speed_key(hit["maximum_memory_speed"])
        for hit in hits
        if speed_key(hit["maximum_memory_speed"])
    }
    if not lookup_speed_keys and not speed_key(actual_speed):
        if not clean_text(family):
            return STATUS_NO_FAMILY, "processor_family is blank in main CSV", lookup_summary
        return (
            STATUS_MATCH,
            "processor matched and maximum memory speed is blank in both main CSV and lookup",
            lookup_summary,
        )
    if not lookup_speed_keys:
        return STATUS_MISSING, "maximum_memory_speed is blank in lookup", lookup_summary
    if not speed_key(actual_speed):
        return STATUS_MISSING, "processor_max_memory_speed is blank in main CSV", lookup_summary
    if speed_key(actual_speed) not in lookup_speed_keys:
        return STATUS_MISMATCH, "main CSV speed does not match lookup maximum_memory_speed", lookup_summary
    if not clean_text(family):
        return STATUS_NO_FAMILY, "processor_family is blank in main CSV", lookup_summary
    return STATUS_MATCH, "processor and maximum memory speed match", lookup_summary


def base_detail(row: dict[str, str], row_number: int) -> dict[str, str]:
    return {
        "server_description": clean_text(row.get("server_description", "")),
        "option_part_no": clean_text(row.get("option_part_no", "")),
    }


def count_columns(counts: Counter[str]) -> dict[str, str]:
    return {
        "match_count": str(counts.get(STATUS_MATCH, 0)),
        "missing_count": str(counts.get(STATUS_MISSING, 0)),
        "mismatch_count": str(counts.get(STATUS_MISMATCH, 0)),
        "no_family_mapped_count": str(counts.get(STATUS_NO_FAMILY, 0)),
        "duplicate_processor_count": str(counts.get(STATUS_DUPLICATE, 0)),
    }


def row_status_from_counts(counts: Counter[str]) -> str:
    statuses = [
        status
        for status in [
            STATUS_MATCH,
            STATUS_MISSING,
            STATUS_MISMATCH,
            STATUS_DUPLICATE,
            STATUS_NO_FAMILY,
        ]
        if counts.get(status, 0)
    ]
    return "|".join(statuses) if statuses else STATUS_MISSING


def row_reason_from_results(row_results: list[dict[str, str]], row_counts: Counter[str]) -> str:
    parts = []
    status_comments = [
        (STATUS_MATCH, "match max memory speed"),
        (STATUS_MISSING, "missing max memory speed"),
        (STATUS_MISMATCH, "mismatch max memory speed"),
        (STATUS_DUPLICATE, "duplicate processor max memory speed"),
        (STATUS_NO_FAMILY, "no family mapped in main CSV"),
    ]
    for status, comment in status_comments:
        count = row_counts.get(status, 0)
        if count:
            parts.append(f"{count} {comment}")
    return "; ".join(parts)


def brand_name(row: dict[str, str]) -> str:
    return clean_text(row.get("A", "")) or "main"


def unique_join(values: list[str]) -> str:
    seen = set()
    output = []
    for value in values:
        value = clean_text(value)
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return "; ".join(output)


def lookup_source_label(lookup_summary: dict[str, str]) -> str:
    source = clean_text(lookup_summary.get("lookup_source", ""))
    if source in {"Intel", "AMD"}:
        return source
    return source or "Intel/AMD"


def status_contains(row_status: str, status: str) -> bool:
    return status in row_status.split("|")


def status_sort_key(status: str) -> tuple[int, str]:
    order = {
        STATUS_MATCH: 0,
        STATUS_MISSING: 1,
        STATUS_MISMATCH: 2,
        STATUS_DUPLICATE: 3,
        STATUS_NO_FAMILY: 4,
    }
    parts = status.split("|")
    return (min(order.get(part, 99) for part in parts), status)


def validate(
    main_csv: Path,
    amd_csv: Path,
    intel_csv: Path,
    detail_out: Path,
    summary_out: Path,
) -> Counter[str]:
    lookup = build_lookup(amd_csv, intel_csv)
    summary: Counter[str] = Counter()

    with detail_out.open("w", encoding="utf-8-sig", newline="") as detail_handle:
        writer = csv.DictWriter(detail_handle, fieldnames=DETAIL_COLUMNS, extrasaction="ignore")
        writer.writeheader()

        for row_number, row in enumerate(read_csv_rows(main_csv), start=2):
            if row_number % 100_000 == 0:
                print(f"Processed {row_number - 1:,} main CSV rows...", file=sys.stderr, flush=True)
            processors = parse_list_cell(row.get("processor", ""))
            speed_entries = parse_list_cell(row.get("processor_max_memory_speed", ""))
            family = clean_text(row.get("processor_family", ""))
            detail = base_detail(row, row_number)
            main_source = brand_name(row)
            row_results: list[dict[str, str]] = []

            if not processors:
                status = STATUS_NO_FAMILY if not family else STATUS_MISSING
                reason = (
                    f"No processor values are present in {brand_name(row)} Crucial processor column."
                    if not family
                    else f"No processor values are present in {brand_name(row)} Crucial processor column."
                )
                row_counts = Counter({status: 1})
                summary.update([status])
                writer.writerow(
                    {
                        **detail,
                        **count_columns(row_counts),
                        "status": status,
                        "reason": reason,
                        "processor": "",
                        "processor_max_memory_speed": "",
                        "maximum_memory_speed": "",
                        "duplicate_processor_data": "",
                        "duplicate_processor_max_memory_speed": "",
                        "missing_processor_max_memory_speed": "",
                        "mismatch_processor_max_memory_speed": "",
                    }
                )
                continue

            speed_by_processor: dict[str, str] = {}
            speed_entries_by_processor: dict[str, list[str]] = defaultdict(list)
            speed_values_by_processor: dict[str, list[str]] = defaultdict(list)
            processor_name_by_key: dict[str, str] = {}
            for entry in speed_entries:
                speed_processor, speeds = parse_processor_speed_entry(entry)
                key = product_key(speed_processor)
                processor_name_by_key.setdefault(key, speed_processor)
                speed_entries_by_processor[key].append(entry)
                speed_values_by_processor[key].extend(speeds)
                if key not in speed_by_processor or not speed_key(speed_by_processor[key]):
                    speed_by_processor[key] = next((speed for speed in speeds if speed_key(speed)), "")

            duplicate_keys = {
                key
                for key, speed_values in speed_values_by_processor.items()
                if len({speed_key(speed) for speed in speed_values if speed_key(speed)}) > 1
            }

            maximum_memory_speeds = []
            duplicate_data_items = []
            duplicate_speed_items = []
            missing_speed_items = []
            mismatch_speed_items = []

            for processor in processors:
                key = product_key(processor)
                actual_speed = speed_by_processor.get(key, "")
                hits = lookup.get(key, [])
                status, reason, lookup_summary = status_for_processor(
                    processor=processor,
                    family=family,
                    actual_speed=actual_speed,
                    hits=hits,
                    is_duplicate_processor=key in duplicate_keys,
                )
                duplicate_data = ""
                duplicate_speed_data = ""
                if key in duplicate_keys:
                    duplicate_processor = processor_name_by_key.get(key, processor)
                    duplicate_data = (
                        f"Duplicate found in main CSV processor_max_memory_speed column: "
                        f"{duplicate_processor}"
                    )
                    duplicate_speed_data = "; ".join(speed_entries_by_processor.get(key, []))

                lookup_speed = lookup_summary.get("lookup_maximum_memory_speed", "")
                lookup_source = lookup_source_label(lookup_summary)
                maximum_memory_speeds.append(lookup_speed)
                if duplicate_data:
                    duplicate_data_items.append(duplicate_data)
                if duplicate_speed_data:
                    duplicate_speed_items.append(duplicate_speed_data)
                if status == STATUS_MISSING:
                    missing_speed_items.append(
                        f"{processor} ({main_source}: {actual_speed or 'blank'}; {lookup_source}: {lookup_speed or 'blank'})"
                    )
                if status == STATUS_MISMATCH:
                    mismatch_speed_items.append(
                        f"{processor} ({main_source}: {actual_speed or 'blank'}; {lookup_source}: {lookup_speed or 'blank'})"
                    )

                row_results.append(
                    {
                        **detail,
                        "status": status,
                        "reason": reason,
                        "processor": processor,
                        "processor_max_memory_speed": actual_speed,
                        "maximum_memory_speed": lookup_speed,
                        "duplicate_processor_data": duplicate_data,
                        "duplicate_processor_max_memory_speed": duplicate_speed_data,
                    }
                )

            row_counts = Counter(result["status"] for result in row_results)
            row_status = row_status_from_counts(row_counts)
            summary.update([row_status])
            writer.writerow(
                {
                    **detail,
                    **count_columns(row_counts),
                    "processor": "; ".join(processors),
                    "status": row_status,
                    "processor_max_memory_speed": "; ".join(speed_entries),
                    "maximum_memory_speed": "; ".join(maximum_memory_speeds),
                    "reason": row_reason_from_results(row_results, row_counts),
                    "duplicate_processor_data": unique_join(duplicate_data_items),
                    "duplicate_processor_max_memory_speed": unique_join(duplicate_speed_items),
                    "missing_processor_max_memory_speed": unique_join(missing_speed_items),
                    "mismatch_processor_max_memory_speed": unique_join(mismatch_speed_items),
                }
            )

    with summary_out.open("w", encoding="utf-8-sig", newline="") as summary_handle:
        writer = csv.DictWriter(summary_handle, fieldnames=["status", "count"])
        writer.writeheader()
        for status in sorted(summary, key=status_sort_key):
            writer.writerow({"status": status, "count": summary.get(status, 0)})

    return summary


def parse_args() -> argparse.Namespace:
    default_output_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Validate a Crucial main CSV processor_max_memory_speed column against AMD/Intel processor CSVs."
    )
    parser.add_argument(
        "--main-csv",
        dest="main_csv",
        type=Path,
        default=MAIN_CSV,
        help="Main vendor Crucial CSV to validate.",
    )
    parser.add_argument("--asrock", dest="main_csv", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--amd", type=Path, default=AMD_CSV)
    parser.add_argument("--intel", type=Path, default=INTEL_CSV)
    parser.add_argument(
        "--detail-out",
        type=Path,
        default=default_output_dir / "processor_memory_speed_status.csv",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=default_output_dir / "processor_memory_speed_summary.csv",
    )
    parser.add_argument(
        "--fail-on",
        nargs="*",
        default=[],
        choices=[
            STATUS_MATCH,
            STATUS_MISSING,
            STATUS_MISMATCH,
            STATUS_DUPLICATE,
            STATUS_NO_FAMILY,
        ],
        help="Optional statuses that should make the command exit with status 1.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.detail_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)

    summary = validate(
        main_csv=args.main_csv,
        amd_csv=args.amd,
        intel_csv=args.intel,
        detail_out=args.detail_out,
        summary_out=args.summary_out,
    )

    print(f"Detail report: {args.detail_out}")
    print(f"Summary report: {args.summary_out}")
    for status in sorted(summary, key=status_sort_key):
        print(f"{status}: {summary.get(status, 0)}")

    failed_statuses = {status: count_rows_with_status(summary, status) for status in args.fail_on}
    failed_statuses = {status: count for status, count in failed_statuses.items() if count}
    if failed_statuses:
        print(f"Failing because these statuses were found: {failed_statuses}", file=sys.stderr)
        return 1
    return 0


def count_rows_with_status(summary: Counter[str], status: str) -> int:
    """Count rows whose combined status (e.g. 'Match|Mismatch') contains `status`."""
    return sum(count for row_status, count in summary.items() if status_contains(row_status, status))


def assert_no_mismatch_or_duplicate(summary: Counter[str]) -> None:
    mismatch_rows = count_rows_with_status(summary, STATUS_MISMATCH)
    duplicate_rows = count_rows_with_status(summary, STATUS_DUPLICATE)
    assert mismatch_rows == 0 and duplicate_rows == 0, (
        f"{mismatch_rows} row(s) contain {STATUS_MISMATCH} and "
        f"{duplicate_rows} row(s) contain {STATUS_DUPLICATE}. "
        f"Row statuses: {dict(summary)}"
    )


def test_no_speed_mismatch_or_ambiguous_duplicate() -> None:
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        summary = validate(
            main_csv=MAIN_CSV,
            amd_csv=AMD_CSV,
            intel_csv=INTEL_CSV,
            detail_out=temp_path / "detail.csv",
            summary_out=temp_path / "summary.csv",
        )

    assert_no_mismatch_or_duplicate(summary)


if __name__ == "__main__":
    raise SystemExit(main())
