"""
Tests for row_level_validator.py (duplicate-row detection + Part URL
validation, incl. the SVID/skip-key exception) plus a regression smoke test
confirming the pre-existing modules still work unchanged.

Run with:  python -m unittest test_row_level_validator -v
"""

import json
import unittest

import csv_reader
import field_matcher as fm
import row_level_validator as rlv
import sample_selector

FIELDNAMES = [
    "part_description", "oem", "oem_part_number", "part_number", "capacity",
    "interface", "form_factor", "DWPD", "category", "part_url",
    "part_specifications", "store", "csv_row_index",
]


def make_row(idx, **overrides):
    row = {
        "part_description": "ABC123",
        "oem": "Dell",
        "oem_part_number": "OEM-1",
        "part_number": "ABC123",
        "capacity": "480 GB",
        "interface": "SATA",
        "form_factor": "2.5",
        "DWPD": "1",
        "category": "ssd",
        "part_url": f"https://example.com/detail?productId={idx}&redirectFrom=ABC123",
        "part_specifications": json.dumps({"SVID": ["12345678"]}),
        "store": "VMware",
        "csv_row_index": idx,
    }
    row.update(overrides)
    return row


class DuplicateDetectionTests(unittest.TestCase):
    def test_two_identical_rows_are_duplicates(self):
        rows = [make_row(0), make_row(1)]
        info = rlv.find_duplicates(rows, FIELDNAMES)
        self.assertTrue(info[0]["is_duplicate"])
        self.assertTrue(info[1]["is_duplicate"])
        self.assertEqual(info[0]["group_id"], info[1]["group_id"])
        self.assertEqual(info[0]["duplicate_of"], [1])
        self.assertEqual(info[1]["duplicate_of"], [0])

    def test_rows_identical_except_part_url_are_duplicates(self):
        rows = [
            make_row(0, part_url="https://example.com/a?redirectFrom=ABC123"),
            make_row(1, part_url="https://example.com/b?redirectFrom=ABC123-different-path"),
        ]
        info = rlv.find_duplicates(rows, FIELDNAMES)
        self.assertTrue(info[0]["is_duplicate"])
        self.assertTrue(info[1]["is_duplicate"])

    def test_rows_with_different_non_url_values_are_not_duplicates(self):
        rows = [make_row(0), make_row(1, oem="HP", part_number="XYZ456")]
        info = rlv.find_duplicates(rows, FIELDNAMES)
        self.assertFalse(info[0]["is_duplicate"])
        self.assertFalse(info[1]["is_duplicate"])
        self.assertIsNone(info[0]["group_id"])

    def test_empty_and_null_spellings_are_treated_as_equal(self):
        rows = [
            make_row(0, capacity=""),
            make_row(1, capacity="nan"),
            make_row(2, capacity="N/A"),
        ]
        info = rlv.find_duplicates(rows, FIELDNAMES)
        self.assertTrue(all(info[i]["is_duplicate"] for i in (0, 1, 2)))
        self.assertEqual(info[0]["group_id"], info[1]["group_id"])
        self.assertEqual(info[0]["group_id"], info[2]["group_id"])

    def test_multiple_duplicate_rows_all_identified(self):
        rows = [make_row(0), make_row(1), make_row(2), make_row(3, oem="Unique")]
        info = rlv.find_duplicates(rows, FIELDNAMES)
        for i in (0, 1, 2):
            self.assertTrue(info[i]["is_duplicate"])
            self.assertEqual(sorted(info[i]["duplicate_of"]), sorted({0, 1, 2} - {i}))
        self.assertFalse(info[3]["is_duplicate"])

    def test_whitespace_is_trimmed_before_comparison(self):
        rows = [make_row(0, oem="Dell"), make_row(1, oem="  Dell  ")]
        info = rlv.find_duplicates(rows, FIELDNAMES)
        self.assertTrue(info[0]["is_duplicate"])
        self.assertTrue(info[1]["is_duplicate"])


class PartUrlValidationTests(unittest.TestCase):
    def test_pass_when_part_description_found_in_redirect_from(self):
        row = make_row(0, part_url="https://example.com/detail?redirectFrom=ABC123",
                        part_specifications=json.dumps({}))
        result = rlv.validate_part_url(row)
        self.assertEqual(result["overall"], rlv.RESULT_MATCHED)
        self.assertEqual(result["field_results"]["part_description"][0], rlv.RESULT_MATCHED)

    def test_fail_when_part_description_missing_from_redirect_from(self):
        row = make_row(0, part_url="https://example.com/detail?redirectFrom=SomethingElse")
        result = rlv.validate_part_url(row)
        self.assertEqual(result["overall"], rlv.RESULT_NOT_FOUND)
        self.assertEqual(result["field_results"]["part_description"][0], rlv.RESULT_NOT_FOUND)

    def test_non_checked_columns_are_not_applicable(self):
        row = make_row(0, part_url="https://example.com/detail?redirectFrom=ABC123")
        result = rlv.validate_part_url(row)
        for physical_col in ("oem", "capacity", "interface", "form_factor", "DWPD"):
            self.assertEqual(result["field_results"][physical_col][0], rlv.RESULT_NOT_APPLICABLE)

    def test_svid_key_present_in_url_skips_value_check(self):
        row = make_row(
            0,
            part_url="https://example.com/detail?redirectFrom=ABC123&svid=1",
            part_specifications=json.dumps({"SVID": ["12345678"]}),
        )
        result = rlv.validate_part_url(row)
        self.assertEqual(result["field_results"]["spec:SVID"][0], rlv.RESULT_SKIPPED)

    def test_svid_key_absent_from_url_applies_normal_validation(self):
        row = make_row(
            0,
            part_url="https://example.com/detail?redirectFrom=ABC123",
            part_specifications=json.dumps({"SVID": ["12345678"]}),
        )
        result = rlv.validate_part_url(row)
        # SVID value "12345678" is not in the redirectFrom text -> NOT FOUND,
        # not silently skipped, since the key itself never appeared in the URL.
        self.assertEqual(result["field_results"]["spec:SVID"][0], rlv.RESULT_NOT_FOUND)

    def test_word_boundary_avoids_false_positive_substring_match(self):
        # "VID" must not be considered present merely because it is a
        # substring of "SVID" in the URL.
        self.assertFalse(rlv.key_token_in_url("VID", "https://example.com/?x=svid"))
        self.assertTrue(rlv.key_token_in_url("SVID", "https://example.com/?x=svid"))


class DuplicateThenUrlOrderingTests(unittest.TestCase):
    def test_duplicate_rows_skip_part_url_validation(self):
        rows = [make_row(0), make_row(1)]
        results = rlv.process_rows(rows, FIELDNAMES)
        for r in results:
            self.assertEqual(r["Duplicate Row"], rlv.DUPLICATE_YES)
            self.assertEqual(r["Part URL Validation Result"], "SKIPPED (DUPLICATE ROW)")

    def test_non_duplicate_row_gets_part_url_validation(self):
        rows = [make_row(0, part_url="https://example.com/detail?redirectFrom=ABC123",
                          part_specifications=json.dumps({})),
                make_row(1, oem="HP", part_number="XYZ456", part_description="XYZ456",
                          part_url="https://example.com/detail?redirectFrom=XYZ456",
                          part_specifications=json.dumps({}))]
        results = rlv.process_rows(rows, FIELDNAMES)
        for r in results:
            self.assertEqual(r["Duplicate Row"], rlv.DUPLICATE_NO)
            self.assertEqual(r["Part URL Validation Result"], rlv.RESULT_MATCHED)


class RegressionSmokeTests(unittest.TestCase):
    """Confirms pre-existing modules still behave as before these changes."""

    def test_csv_reader_loads_real_source_file(self):
        rows = csv_reader.load_csv()
        self.assertGreater(len(rows), 0)
        self.assertIn(csv_reader.CSV_COLUMNS["part_url"], rows[0])
        self.assertIn(csv_reader.CSV_COLUMNS["mfr_part_number"], rows[0])
        self.assertIn(csv_reader.CSV_COLUMNS["part_specification"], rows[0])

    def test_sample_selector_still_works_against_real_data(self):
        rows = csv_reader.load_csv()
        selected, warnings = sample_selector.select_sample(rows, 2, 2, seed=1)
        self.assertEqual(len(selected), 4)

    def test_field_matcher_unchanged_behaviour(self):
        self.assertEqual(fm.compare_dwpd("1", "1.0")[0], fm.RESULT_MATCHED)
        self.assertEqual(fm.compare_capacity("480 GB", "480GB")[0], fm.RESULT_MATCHED)
        self.assertEqual(fm.compare_form_factor('2.5"', "2.5-inch")[0], fm.RESULT_MATCHED)


if __name__ == "__main__":
    unittest.main()
