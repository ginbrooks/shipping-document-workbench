"""Deterministic, coverage-aware review of transcribed shipping documents.

This module compares supplied transcriptions. It does not extract, authenticate,
or approve source attachments, and a passing report is not permission to ship.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re

from .expectations import resolve_expected
from .models import Shipment, leaves

RULES_VERSION = "preflight-1.0"
NOTICE = "仅核对所提供的抄录值与确认基准；不验证原件真伪，不代表出运放行。"
MESSAGES = {
    "PASS": "数值相符，来源已人工核验",
    "MISSING": "未提供必填值，请补录对应原件字段",
    "BASELINE_UNCONFIRMED": "业务基准或计算依赖尚未确认",
    "SOURCE_MISSING": "缺少来源文件或页码 / 单元格定位",
    "SOURCE_MISMATCH": "单证、字段、角色或合同范围不匹配",
    "DUPLICATE": "同一字段存在多条记录，请明确保留哪一条",
    "INVALID": "数值格式或单位不受支持，请核对原件",
    "MISMATCH": "原件抄录值与当前确认基准不同",
    "NEEDS_REVIEW": "值相符，仍需人工核验来源",
}


def _text(value):
    return " ".join(str(value).split()) if value is not None else ""


def _confirmed(shipment, path):
    from .validation import get_path
    fact = shipment.get("facts", {}).get(path, {})
    return bool(fact.get("confirmed") and fact.get("value") == get_path(shipment, path))


def checklist(shipment, scope):
    """A focused invoice / packing-list / carrier-return checklist per contract.

    Required checks are generated from the shipment, never from observations:
    deleting a source row cannot silently remove its requirement.
    """
    contracts = [(i, c) for i, c in enumerate(shipment["contracts"]) if c["id"] == scope]
    if len(contracts) != 1:
        raise ValueError("预检范围必须是一个明确的合同 ID")
    ci, contract = contracts[0]
    lines = [(i, line) for i, line in enumerate(shipment["lines"]) if line["contract_id"] == scope]
    if not lines:
        raise ValueError("合同没有货物行，不能建立预检清单")
    specs = [
        ("invoice", "客户发票", "customer_pdf", f"contracts.{ci}.invoice_no", "发票号", None),
        ("invoice", "客户发票", "customer_pdf", "consignee.name_en", "收货人", None),
        ("invoice", "客户发票", "customer_pdf", "prices.customer.currency", "客户币种", None),
        ("invoice", "客户发票", "customer_pdf", "prices.customer.total_amount", "客户合计金额", "amount"),
        ("carrier", "货代回件", "carrier_return", "destination", "目的地", None),
    ]
    for i, line in lines:
        label = line.get("name_en") or line["id"]
        for doc, doc_label, role, suffix, field, unit in [
            ("invoice", "客户发票", "customer_pdf", "base_quantity", "基础数量", line.get("base_unit")),
            ("packing", "装箱单", "packing_list", "batch_no", "批号", None),
            ("packing", "装箱单", "packing_list", "base_quantity", "基础数量", line.get("base_unit")),
            ("packing", "装箱单", "packing_list", "full_cartons", "满箱箱数", "carton"),
            ("packing", "装箱单", "packing_list", "packing_spec.carton_gross_g", "单箱毛重", "g"),
        ]:
            specs.append((doc, doc_label, role, f"lines.{i}.{suffix}", f"{field} · {label}", unit))
    checks = []
    for doc, label, role, path, field, unit in specs:
        value, confirmed, valid = resolve_expected(shipment, None, path, scope)
        if path.startswith("lines."):
            line_prefix = ".".join(path.split(".")[:2])
            confirmed = confirmed and _confirmed(shipment, line_prefix + ".contract_id")
            if path.endswith(".base_quantity"):
                confirmed = confirmed and bool(unit) and _confirmed(shipment, line_prefix + ".base_unit")
        if path.startswith("prices."):
            # Every price/quantity/currency/adjustment contributing to the total
            # must still match a confirmed fact, including zero-value adjustments.
            currency = contract.get("customer_currency")
            deps = [f"contracts.{ci}.customer_currency", f"currency_decimals.{currency}"]
            for i, line in lines:
                deps.extend(f"lines.{i}.{key}" for key in ("base_quantity", "base_unit", "contract_id"))
                # Conversion meaning depends on packaging units as well as the
                # factor saved in Price. Confirm declared conversion inputs too.
                deps.extend(f"lines.{i}.{key}" for key in ("inner_unit", "units_per_inner", "inners_per_carton") if line.get(key) is not None)
                deps.extend(p for p, v in leaves(line["customer_price"], f"lines.{i}.customer_price") if v is not None)
            deps.extend(p for p, v in leaves(contract["customer_adjustments"], f"contracts.{ci}.customer_adjustments") if v is not None)
            confirmed = confirmed and bool(currency) and all(line.get("base_unit") for _, line in lines) and all(_confirmed(shipment, p) for p in deps)
        checks.append({
            "id": f"{doc}:{path}", "document_id": doc, "document_label": label,
            "doc_role": role, "scope": scope, "field_path": path, "field_label": field,
            "expected": str(value) if value is not None else None,
            "input_confirmed": bool(valid and confirmed and value is not None),
            "value_type": "number" if unit is not None else "text", "base_unit": unit,
        })
    return checks


def normalize_number(value, source_unit=None, base_unit=None):
    """Exact, nonnegative decimals; no float conversion or observation rounding."""
    raw = str(value).strip()
    if len(raw) > 80 or not re.fullmatch(r"\+?\d+(?:\.\d+)?", raw):
        raise ValueError("INVALID_DECIMAL")
    try:
        number = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError("INVALID_DECIMAL") from error
    if source_unit == base_unit or not source_unit and not base_unit:
        return number
    if base_unit == "g" and source_unit == "kg":
        with localcontext() as context:
            context.prec = 100
            return number * Decimal(1000)
    raise ValueError("UNIT_MISMATCH")


def evaluate_check(check):
    """Shared small rule contract mirrored by the static browser demo."""
    if not _text(check.get("value")):
        status = "MISSING"
    elif not check.get("input_confirmed"):
        status = "BASELINE_UNCONFIRMED"
    elif not _text(check.get("source_file")) or not _text(check.get("source_locator")):
        status = "SOURCE_MISSING"
    else:
        try:
            if check["value_type"] == "number":
                expected = normalize_number(check["expected"], check.get("base_unit"), check.get("base_unit"))
                observed = normalize_number(check["value"], check.get("source_unit"), check.get("base_unit"))
                equal = expected == observed
            else:
                equal = _text(check["expected"]) == _text(check["value"])
            status = "MISMATCH" if not equal else "PASS" if check.get("reviewed") is True else "NEEDS_REVIEW"
        except (ValueError, InvalidOperation):
            status = "INVALID"
    return dict(check, status=status, message=MESSAGES[status])


def input_fingerprint(shipment, observations, scope, documents):
    payload = {"rules_version": RULES_VERSION, "shipment": shipment,
               "observations": observations, "scope": scope, "documents": documents}
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def build_preflight(shipment, observations, scope, documents, *, synthetic_only=False):
    """Return all required checks plus invalid/duplicate source records.

    documents is a declared manifest, not proof of attachment authenticity.
    Integration with real files should supply verified source metadata separately.
    """
    shipment = Shipment.model_validate(shipment).model_dump(mode="json")
    required = checklist(shipment, scope)
    ids = [d["id"] for d in documents]
    if len(ids) != len(set(ids)):
        raise ValueError("单证清单包含重复 ID")
    manifest = {d["id"]: d for d in documents}
    results = []
    known_ids = {c["id"] for c in required}
    unexpected = [deepcopy(o) for o in observations if o.get("check_id") not in known_ids]
    for check in required:
        matches = [o for o in observations if o.get("check_id") == check["id"]]
        record = matches[0] if matches else {}
        doc = manifest.get(check["document_id"], {})
        row = dict(check, value=record.get("value"), source_unit=record.get("unit"),
                   source_file=record.get("source_file", ""), source_locator=record.get("source_locator", ""),
                   reviewed=record.get("reviewed") is True)
        result = evaluate_check(row)
        if len(matches) > 1:
            result.update(status="DUPLICATE", message=MESSAGES["DUPLICATE"], conflicting_records=deepcopy(matches))
        elif matches and (not doc or any(record.get(k) != check[k] for k in ("document_id", "scope", "field_path", "doc_role"))
                          or doc.get("scope") != scope or doc.get("doc_role") != check["doc_role"]
                          or record.get("source_file") != doc.get("file_name")):
            result.update(status="SOURCE_MISMATCH", message=MESSAGES["SOURCE_MISMATCH"])
        results.append(result)
    counts = dict(Counter(r["status"] for r in results))
    from .validation import validate_shipment
    scoped = dict(shipment, contracts=[c for c in shipment["contracts"] if c["id"] == scope],
                  lines=[line for line in shipment["lines"] if line["contract_id"] == scope])
    business_checks = validate_shipment(scoped)
    unresolved = (sum(r["status"] != "PASS" for r in results) + len(unexpected)
                  + sum(r["status"] != "PASS" for r in business_checks))
    return {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "synthetic_only": synthetic_only, "notice": NOTICE,
        "evidence_boundary": "supplied_transcriptions_and_declared_source_locations_only",
        "created_at": datetime.now(timezone.utc).isoformat(), "scope": scope,
        "input_fingerprint": input_fingerprint(shipment, observations, scope, documents),
        "status": "READY_FOR_HUMAN_REVIEW" if unresolved == 0 else "BLOCKED",
        "summary": {"required": len(results), "passed": counts.get("PASS", 0), "unresolved": unresolved, "by_status": counts},
        "checks": results, "business_checks": business_checks, "unexpected_records": unexpected,
    }


def is_report_current(report, shipment, observations, scope, documents):
    normalized = Shipment.model_validate(shipment).model_dump(mode="json")
    return report.get("input_fingerprint") == input_fingerprint(normalized, observations, scope, documents)


def demo_packet(corrected=False):
    """Generate entirely synthetic, explicitly transcribed source records."""
    from .demo import synthetic
    shipment = synthetic()
    scope = shipment["contracts"][0]["id"]
    required = checklist(shipment, scope)
    documents = [{"id": doc, "doc_role": role, "scope": scope, "file_name": filename}
                 for doc, role, filename in [("invoice", "customer_pdf", "SYNTHETIC-invoice.txt"),
                                              ("packing", "packing_list", "SYNTHETIC-packing-list.txt"),
                                              ("carrier", "carrier_return", "SYNTHETIC-carrier-return.txt")]]
    files = {d["id"]: d["file_name"] for d in documents}
    observations = []
    source_lines = Counter()
    for i, check in enumerate(required):
        unit = check["base_unit"]
        value = check["expected"]
        if unit == "g":
            value, unit = str(Decimal(value) / 1000), "kg"
        source_lines[check["document_id"]] += 1
        observations.append({k: check[k] for k in ("document_id", "doc_role", "scope", "field_path")}
                            | {"check_id": check["id"], "value": value, "unit": unit,
                               "source_file": files[check["document_id"]], "source_locator": f"合成文本第 {source_lines[check['document_id']] + 2} 行",
                               "reviewed": True})
    if not corrected:
        for record in observations:
            if record["check_id"] == "invoice:prices.customer.total_amount":
                record["value"] = str(Decimal(record["value"]) + Decimal("12.50"))
            elif record["check_id"] == "packing:lines.0.batch_no":
                record["value"] = ""
            elif record["check_id"] == "packing:lines.0.base_quantity":
                record["value"] = "360600"
            elif record["check_id"] == "carrier:destination":
                record["reviewed"] = False
    return {"shipment": shipment, "observations": observations, "scope": scope, "documents": documents, "synthetic_only": True}


def browser_demo():
    broken = build_preflight(**demo_packet())
    corrected = build_preflight(**demo_packet(corrected=True))
    return {"synthetic_only": True, "rules_version": RULES_VERSION,
            "notice": "浏览器本地合成样例。基准由 Python 计算并确认；不读取私人文件，不连接模型，不代表出运放行。",
            "checks": broken["checks"], "corrected_checks": corrected["checks"]}
