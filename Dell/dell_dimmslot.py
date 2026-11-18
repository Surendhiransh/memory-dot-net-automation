import re
import os
import ast
import pandas as pd
from pdfminer.high_level import extract_text

# =========================
# CONFIGURATION
# =========================
# Override with the DELL_CSV_PATH / DELL_PDF_DIR environment variables.
CSV_PATH = os.environ.get("DELL_CSV_PATH", "/mnt/data/dell.csv")
PDF_DIR = os.environ.get("DELL_PDF_DIR", "/mnt/data")

# =========================
# UTILITIES
# =========================

WORD_TO_NUM = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "eight": 8,
    "sixteen": 16
}

def normalize_number(value):
    value = value.lower()
    if value.isdigit():
        return int(value)
    return WORD_TO_NUM.get(value)

# =========================
# PDF EXTRACTION
# =========================

def extract_pdf_text(pdf_path):
    return extract_text(pdf_path).lower()

# =========================
# SECTION EXTRACTION
# =========================

def extract_memory_section(text):
    start_patterns = [
        r'\bmemory specifications\b',
        r'\bmemory\b'
    ]

    end_patterns = [
        r'\bports and connectors\b',
        r'\bexternal ports\b',
        r'\bcommunications\b',
        r'\bstorage\b'
    ]

    start_index = None
    end_index = None

    for pattern in start_patterns:
        match = re.search(pattern, text)
        if match:
            start_index = match.start()
            break

    if start_index is None:
        return ""

    for pattern in end_patterns:
        match = re.search(pattern, text[start_index:])
        if match:
            end_index = start_index + match.start()
            break

    return text[start_index:end_index] if end_index else text[start_index:]

# =========================
# DIMM SLOTS EXTRACTION
# =========================

def extract_dimm_slots(text):
    patterns = [
        r'dimm slots?\s*[:\-]?\s*(\w+)',
        r'dimmslots?\s*[:\-]?\s*(\w+)',
        r'memory slots?\s*[:\-]?\s*(\w+)',
        r'slots?\s*up to\s*(\w+)'
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return normalize_number(match.group(1))

    return None

# =========================
# MAX MEMORY EXTRACTION
# =========================
def extract_max_memory(text):
    matches = re.findall(r'(\d+)\s*(gb|tb)', text)
    memory_values = []

    for value, unit in matches:
        value = int(value)
        if unit == 'tb':
            value *= 1024
        memory_values.append(value)

    return max(memory_values) if memory_values else None

# =========================
# MAIN TEST LOGIC
# =========================

def validate_pdfs(csv_path=CSV_PATH, pdf_dir=PDF_DIR):
    """
    Validate every CSV row against its referenced PDF.
    Returns a list of failure messages; an empty list means every row passed.
    A missing or unnamed referenced PDF is a failure, not a skip.
    """
    df = pd.read_csv(csv_path)
    failures = []

    for index, row in df.iterrows():
        pdf_file = row.get("file_name")
        if pd.isna(pdf_file) or not str(pdf_file).strip():
            failures.append(f"row {index}: file_name is blank, no PDF to validate")
            print(f"FAIL row {index}: file_name is blank")
            continue

        pdf_file = str(pdf_file).strip()
        pdf_path = os.path.join(pdf_dir, pdf_file)

        if not os.path.exists(pdf_path):
            failures.append(f"row {index}: referenced PDF not found: {pdf_path}")
            print(f"FAIL PDF not found: {pdf_file}")
            continue

        print(f"\nValidating: {pdf_file}")

        try:
            full_text = extract_pdf_text(pdf_path)
            memory_text = extract_memory_section(full_text)

            pdf_dimm = extract_dimm_slots(memory_text)
            pdf_max_mem = extract_max_memory(memory_text)

            csv_dimm = int(ast.literal_eval(row["dimm_slots"])[0])
            csv_max_mem = int(
                ast.literal_eval(row["maximum_memory"])[0]
                .replace("GB", "")
                .strip()
            )
        except Exception as exc:
            failures.append(f"row {index}: could not validate {pdf_file}: {exc}")
            print(f"FAIL {pdf_file}: {exc}")
            continue

        row_failures = []
        if pdf_dimm != csv_dimm:
            row_failures.append(
                f"row {index}: DIMM SLOT MISMATCH in {pdf_file}: PDF={pdf_dimm}, CSV={csv_dimm}"
            )
        if pdf_max_mem != csv_max_mem:
            row_failures.append(
                f"row {index}: MAX MEMORY MISMATCH in {pdf_file}: "
                f"PDF={pdf_max_mem}GB, CSV={csv_max_mem}GB"
            )

        if row_failures:
            failures.extend(row_failures)
            print("FAIL " + "; ".join(row_failures))
        else:
            print("PASSED")

    return failures


def test_pdf_vs_csv():
    failures = validate_pdfs(CSV_PATH, PDF_DIR)
    assert not failures, f"{len(failures)} PDF validation failure(s):\n" + "\n".join(failures)

# =========================
# RUN
# =========================

if __name__ == "__main__":
    import sys
    failures = validate_pdfs(CSV_PATH, PDF_DIR)
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
