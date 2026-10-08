from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import shutil
import subprocess
import sys
import re

import pytest

from shipping.preflight import (browser_demo, build_preflight, checklist, demo_packet,
                                evaluate_check, is_report_current, normalize_number)


def test_demo_exposes_four_review_actions_and_corrected_passes():
    broken = build_preflight(**demo_packet())
    assert broken["status"] == "BLOCKED"
    assert broken["summary"]["by_status"] == {"PASS": 6, "MISMATCH": 2, "NEEDS_REVIEW": 1, "MISSING": 1}
    corrected = build_preflight(**demo_packet(True))
    assert corrected["status"] == "READY_FOR_HUMAN_REVIEW"
    assert corrected["summary"]["passed"] == 10
    assert corrected["synthetic_only"]
    json.dumps(corrected)


def test_deleting_observation_does_not_remove_required_check():
    packet = demo_packet(True)
    packet["observations"].pop()
    report = build_preflight(**packet)
    assert report["summary"]["required"] == 10
    assert report["summary"]["by_status"]["MISSING"] == 1
    assert report["status"] == "BLOCKED"


@pytest.mark.parametrize("field,value", [("scope", "ANOTHER-CONTRACT"), ("scope", None),
    ("field_path", "lines.99.batch_no"), ("doc_role", "purchase_invoice"),
    ("source_file", "unregistered.txt"), ("document_id", "other")])
def test_source_binding_and_scope_must_match(field, value):
    packet = demo_packet(True)
    packet["observations"][0][field] = value
    report = build_preflight(**packet)
    assert report["checks"][0]["status"] == "SOURCE_MISMATCH"
    assert report["status"] == "BLOCKED"


def test_duplicate_and_unexpected_records_are_never_silently_ignored():
    packet = demo_packet(True)
    packet["observations"].append(deepcopy(packet["observations"][0]))
    packet["observations"].append(dict(packet["observations"][0], check_id="unknown"))
    report = build_preflight(**packet)
    assert report["checks"][0]["status"] == "DUPLICATE"
    assert len(report["checks"][0]["conflicting_records"]) == 2
    assert len(report["unexpected_records"]) == 1
    assert report["summary"]["unresolved"] == 2


def test_unconfirmed_changed_baseline_does_not_pass_matching_source():
    packet = demo_packet(True)
    packet["shipment"]["destination"] = "CHANGED"
    record = next(o for o in packet["observations"] if o["check_id"] == "carrier:destination")
    record["value"] = "CHANGED"
    report = build_preflight(**packet)
    assert next(c for c in report["checks"] if c["id"] == record["check_id"])["status"] == "BASELINE_UNCONFIRMED"


def test_price_adjustment_requires_confirmation_even_for_zero():
    packet = demo_packet(True)
    packet["shipment"]["contracts"][0]["customer_adjustments"] = [
        {"label": "SYNTHETIC adjustment", "amount": "0", "evidence": {"manual_note": "not confirmed"}}]
    report = build_preflight(**packet)
    amount = next(c for c in report["checks"] if c["field_path"] == "prices.customer.total_amount")
    assert amount["status"] == "BASELINE_UNCONFIRMED"


@pytest.mark.parametrize("path", ["lines.0.base_unit", "lines.0.units_per_inner", "lines.0.inners_per_carton", "currency_decimals.EUR"])
def test_price_conversion_and_rounding_dependencies_must_be_confirmed(path):
    packet = demo_packet(True)
    packet["shipment"]["facts"][path]["confirmed"] = False
    report = build_preflight(**packet)
    amount = next(c for c in report["checks"] if c["field_path"] == "prices.customer.total_amount")
    assert amount["status"] == "BASELINE_UNCONFIRMED"
    assert report["status"] == "BLOCKED"


def test_quantity_requires_confirmed_nonempty_unit():
    packet = demo_packet(True)
    packet["shipment"]["facts"]["lines.0.base_unit"]["confirmed"] = False
    report = build_preflight(**packet)
    quantities = [c for c in report["checks"] if c["field_path"] == "lines.0.base_quantity"]
    assert len(quantities) == 2
    assert all(c["status"] == "BASELINE_UNCONFIRMED" for c in quantities)
    packet["shipment"]["lines"][0]["base_unit"] = None
    packet["shipment"]["facts"]["lines.0.base_unit"] = {"value": None, "confirmed": True, "source_ref": {"manual_note": "SYNTHETIC"}}
    report = build_preflight(**packet)
    assert all(c["status"] == "BASELINE_UNCONFIRMED" for c in report["checks"] if c["field_path"] == "lines.0.base_quantity")


def test_passing_document_values_do_not_override_invalid_packing_arithmetic():
    packet = demo_packet(True)
    packet["shipment"]["lines"][0]["units_per_inner"] = 26
    report = build_preflight(**packet)
    assert report["status"] == "BLOCKED"
    assert any(c["status"] == "FAIL" and c["code"] == "Q01" for c in report["business_checks"])


def test_report_invalidates_on_source_confirmation_manifest_or_data_changes():
    packet = demo_packet(True)
    report = build_preflight(**packet)
    args = {k: v for k, v in packet.items() if k != "synthetic_only"}
    assert is_report_current(report, **args)
    for part, mutate in [
        ("observations", lambda v: v[0].update(reviewed=False)),
        ("documents", lambda v: v[0].update(file_name="changed.txt")),
        ("shipment", lambda v: v.update(destination="CHANGED")),
    ]:
        changed = deepcopy(args)
        mutate(changed[part])
        assert not is_report_current(report, **changed)


def test_whitespace_location_is_missing_and_unreviewed_matching_value_is_pending():
    row = browser_demo()["corrected_checks"][0]
    assert evaluate_check(dict(row, source_locator=" \n "))["status"] == "SOURCE_MISSING"
    assert evaluate_check(dict(row, reviewed=False))["status"] == "NEEDS_REVIEW"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1e3", "1,000", "0x10", "1" * 81])
def test_invalid_numbers_never_pass(value):
    with pytest.raises(ValueError):
        normalize_number(value)


def test_exact_decimal_and_unit_conversion():
    assert normalize_number("5.000", "kg", "g") == Decimal("5000")
    assert normalize_number("123456789012345678901234567890.1", "kg", "g") == Decimal("123456789012345678901234567890100")
    with pytest.raises(ValueError):
        normalize_number("5000", "lb", "g")
    row = next(c for c in browser_demo()["corrected_checks"] if c["base_unit"] == "amount")
    assert evaluate_check(dict(row, value=str(Decimal(row["expected"]) + Decimal("0.001"))))["status"] == "MISMATCH"


def test_omitted_source_unit_is_not_silently_assumed():
    packet = demo_packet(True)
    record = next(o for o in packet["observations"] if o["field_path"].endswith("carton_gross_g"))
    record["value"] = "5000"
    record.pop("unit")
    report = build_preflight(**packet)
    assert next(c for c in report["checks"] if c["id"] == record["check_id"])["status"] == "INVALID"
    assert report["status"] == "BLOCKED"


@pytest.mark.parametrize("scope", [None, "", "ALL", "unknown"])
def test_explicit_contract_required(scope):
    with pytest.raises(ValueError):
        checklist(demo_packet()["shipment"], scope)


def test_browser_snapshot_is_generated_from_current_python_rules():
    path = Path(__file__).resolve().parents[1] / "docs/demo/data.js"
    data = json.loads(path.read_text().split("window.PREFLIGHT_DEMO = ", 1)[1].removesuffix(";\n"))
    assert data == browser_demo()


def test_every_demo_source_locator_points_to_its_actual_synthetic_line():
    root = Path(__file__).resolve().parents[1] / "docs/demo/sources"
    for scenario, corrected in [("problem", False), ("corrected", True)]:
        packet = demo_packet(corrected)
        for observation in packet["observations"]:
            lines = (root / scenario / observation["source_file"]).read_text().splitlines()
            number = int(re.search(r"第 (\d+) 行", observation["source_locator"]).group(1))
            assert lines[0].startswith("SYNTHETIC TEST DOCUMENT")
            assert (observation["value"] or "[未提供]") in lines[number - 1]


@pytest.mark.parametrize("corrected,exit_code", [(False, 2), (True, 0)])
def test_cli_accepts_packet_json_and_reports_machine_readable_status(tmp_path, corrected, exit_code):
    packet = demo_packet(corrected)
    packet.pop("synthetic_only")
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(packet))
    output = tmp_path / "report"
    run = subprocess.run([sys.executable, "scripts/preflight_demo.py", "--input", str(input_path), "--output", str(output)],
                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert run.returncode == exit_code, run.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["synthetic_only"] is False
    assert report["summary"]["unresolved"] == (0 if corrected else 4)


def test_cli_handles_invalid_input_without_a_report(tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text("{}")
    output = tmp_path / "report"
    run = subprocess.run([sys.executable, "scripts/preflight_demo.py", "--input", str(input_path), "--output", str(output)],
                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert run.returncode == 1
    assert "预检输入无效" in run.stderr
    assert not (output / "report.json").exists()


def test_cli_handles_out_of_range_amount_without_traceback(tmp_path):
    packet = demo_packet(True)
    packet["shipment"]["lines"][0]["customer_price"]["unit_price"] = "9" * 100
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(packet))
    run = subprocess.run([sys.executable, "scripts/preflight_demo.py", "--input", str(input_path), "--output", str(tmp_path / "report")],
                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert run.returncode == 1
    assert "数值计算超出支持的精度" in run.stderr
    assert "Traceback" not in run.stderr


def test_browser_rule_parity():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed only for browser rule parity; CI runs this check")
    rows = browser_demo()["checks"] + browser_demo()["corrected_checks"]
    numeric = next(r for r in rows if r["base_unit"] == "g")
    text = rows[0]
    for value in ("0", "5.000", "5000", "NaN", "1e3", "-1", "1,000", "9" * 81, "123456789012345678901234567890.1"):
        rows.append(dict(numeric, value=value))
    rows += [dict(numeric, source_unit="lb"), dict(text, source_locator=" "), dict(text, input_confirmed=False),
             dict(text, value=""), dict(text, reviewed=False), dict(text, value="  " + text["expected"] + "  ")]
    script = "const fs=require('fs');const rules=require('./docs/demo/app.js');process.stdout.write(JSON.stringify(rules.evaluateChecks(JSON.parse(fs.readFileSync(0,'utf8'))).map(x=>x.status)));"
    run = subprocess.run([node, "-e", script], input=json.dumps(rows), capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[1], check=True)
    assert json.loads(run.stdout) == [evaluate_check(row)["status"] for row in rows]
