from __future__ import annotations

import json
from pathlib import Path
from typing import Any


RULES_PATH = Path(__file__).resolve().parents[1] / "configs" / "regulatory_rules.json"


def load_rules() -> dict[str, Any]:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def normalize_framework(framework: str | None, standards: list[str] | None = None) -> str:
    rules = load_rules()
    if framework and framework != "AUTO" and framework in rules:
        return framework
    standards_text = " ".join(standards or []).lower()
    if "iso 13485" in standards_text:
        return "ISO_13485"
    if "mdr" in standards_text or "annex" in standards_text:
        return "EU_MDR"
    return "FDA_21_CFR_820"


def regulatory_reference(framework: str | None, standard: str | None = None) -> str:
    rules = load_rules()
    resolved = normalize_framework(framework, [standard] if standard else [])
    if standard:
        return standard
    return rules.get(resolved, rules["FDA_21_CFR_820"])["default_reference"]


def regulatory_label(framework: str | None, standards: list[str] | None = None) -> str:
    rules = load_rules()
    resolved = normalize_framework(framework, standards)
    return rules.get(resolved, rules["FDA_21_CFR_820"])["label"]
