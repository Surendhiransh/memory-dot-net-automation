"""Offline unit tests for the memory / USB / SSD rules (no browser, runs in under a second).

Usage   : python -m pytest tests/test_unit_parts.py
"""
from kingston_qa.parts import (PartCheck, PartRow, PartStatus, _check_card_columns, _check_page_columns,
                               category_key, compare_value, overall_status)

CARD = {"part_number": "KCP432SS8/8", "is_memory_card": True, "data_category": None,
        "tab_label": "System-specific memory", "first_download_url": None, "first_download_is_datasheet": False,
        "part_image_url": "https://media.kingston.com/x-tn.png", "part_description": "8GB DDR4 SODIMM",
        "description": "DDR4 3200MT/s CL22"}
PAGE = "https://www.kingston.com/en/memory/search/model/1/acer-x"
ROW = PartRow(1, {"store": "Kingston", "server_description": "Acer- X", "host_url": PAGE, "category": "memory",
                  "part_number": "KCP432SS8/8", "capacity": "", "form_factor": "", "part_url": PAGE,
                  "datasheet_url": "", "part_image_url": "https://media.kingston.com/x-tn.png",
                  "part_description": "8GB DDR4 SODIMM", "description": "DDR4 3200MT/s CL22",
                  "part_specification": '{"General": {"part_number": ["KCP432SS8/8"], "part_description": '
                                        '["8GB DDR4 SODIMM"], "category": ["memory"]}, "Description": '
                                        '{"description": ["DDR4 3200MT/s CL22"]}}', "status": "Available"})


def test_compare_value_statuses():
    assert compare_value("16GB", "16GB", "x")[0] is PartStatus.MATCHED
    assert compare_value("", "", "x")[0] is PartStatus.MATCHED
    status, comment = compare_value("16GB", "32GB", "capacity")
    assert status is PartStatus.MISMATCHED and comment == "CSV shows '16GB', website shows '32GB'."
    assert compare_value("16GB", None, "x")[0] is PartStatus.UNMATCHED
    assert compare_value("", "32GB", "x")[0] is PartStatus.UNMATCHED


def test_category_from_tab_label_maps_to_csv_key():
    assert category_key(CARD)[0] == "memory"
    assert category_key({**CARD, "tab_label": "Server Premier"})[0] == "memory"
    assert category_key({**CARD, "is_memory_card": False, "tab_label": "Server Premier"})[0] == "server premier"
    assert category_key({**CARD, "data_category": "Solid-State Drives"})[0] == "ssd"


def test_all_card_and_page_columns_match():
    checks = _check_page_columns(ROW, {"url": PAGE + "?status=active", "description": "Acer - X"})
    checks += _check_card_columns(ROW, CARD, PAGE + "?status=active")
    assert {c.column: c.status for c in checks if c.status is not PartStatus.MATCHED} == {}


def test_datasheet_drives_part_url():
    card = {**CARD, "first_download_url": "https://www.kingston.com/datasheets/A.pdf", "first_download_is_datasheet": True}
    row = PartRow(1, {**ROW.data, "part_url": "https://www.kingston.com/datasheets/A.pdf",
                      "datasheet_url": "https://www.kingston.com/datasheets/A.pdf"})
    by_col = {c.column: c.status for c in _check_card_columns(row, card, PAGE)}
    assert by_col["part_url"] is PartStatus.MATCHED and by_col["datasheet_url"] is PartStatus.MATCHED
    # a PCN link in first position is not a datasheet -> part_url falls back to the page URL
    pcn = {**CARD, "first_download_url": "https://www.kingston.com/en/memory/search/pcn?partid=X"}
    assert {c.column: c.status for c in _check_card_columns(ROW, pcn, PAGE)}["part_url"] is PartStatus.MATCHED


def test_overall_precedence():
    def chk(s):
        return PartCheck("c", "", "", s, "")
    assert overall_status([chk(PartStatus.MATCHED), chk(PartStatus.SKIPPED)], False) is PartStatus.MATCHED
    assert overall_status([chk(PartStatus.UNMATCHED), chk(PartStatus.MISMATCHED)], False) is PartStatus.MISMATCHED
    assert overall_status([chk(PartStatus.UNMATCHED)], False) is PartStatus.UNMATCHED
    assert overall_status([chk(PartStatus.MATCHED)], True) is PartStatus.BLOCKED


def test_ssd_second_option_is_form_factor_or_other_group():
    from kingston_qa.ssd import second_option
    assert second_option({"selected": {"Capacity": "256GB", "Form Factor": "mSATA"}}) == ("mSATA", "Form Factor")
    assert second_option({"selected": {"Capacity": "240GB", "Heatsink": "Non-Heatsink"}}) == ("Non-Heatsink", "Heatsink")
    assert second_option({"selected": {"Capacity": "2048GB"}}) == (None, None)
    assert second_option({"selected": {"Capacity": "480GB", "Security Features": "None"}}) == (None, "Security Features")


def test_capacity_and_form_factor_read_from_card_attributes():
    from kingston_qa.parts import _attr_checks
    usb = {**CARD, "data_capacity": "128", "data_form_factor": None}
    by_col = {c.column: c for c in _attr_checks(PartRow(1, {**ROW.data, "capacity": ""}), usb)}
    assert by_col["capacity"].status is PartStatus.UNMATCHED and "128" in by_col["capacity"].comment
    assert by_col["form_factor"].status is PartStatus.MATCHED
    by_col = {c.column: c for c in _attr_checks(PartRow(1, {**ROW.data, "capacity": "128GB"}), usb)}
    assert by_col["capacity"].status is PartStatus.MATCHED
    memory = {**CARD, "data_capacity": None, "data_form_factor": None}
    assert all(c.status is PartStatus.MATCHED for c in _attr_checks(ROW, memory))
