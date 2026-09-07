from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


DEFAULT_GOLDEN_SET_PATH = Path(__file__).with_name("golden_set.yaml")


@dataclass(frozen=True)
class GoldenScenario:
    id: str
    category: str
    setup: Dict[str, Any]
    user_input: str
    expected_behavior: str
    forbidden_behavior: List[str]
    verification: List[str]
    expected: Dict[str, Any]


@dataclass(frozen=True)
class GoldenSet:
    version: int
    scenarios: List[GoldenScenario]


def load_golden_set(path: Optional[Path] = None) -> GoldenSet:
    source_path = path or DEFAULT_GOLDEN_SET_PATH
    payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Golden Set version must be 1")

    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ValueError("Golden Set must contain scenarios")

    scenarios: List[GoldenScenario] = []
    seen_ids = set()
    required_fields = (
        "id",
        "category",
        "setup",
        "user_input",
        "expected_behavior",
        "forbidden_behavior",
        "verification",
    )
    for raw in raw_scenarios:
        if not isinstance(raw, dict):
            raise ValueError("Golden Set scenarios must be mappings")
        missing = [field for field in required_fields if field not in raw]
        if missing:
            raise ValueError(f"Golden scenario is missing: {', '.join(missing)}")
        scenario_id = _required_text(raw, "id")
        if scenario_id in seen_ids:
            raise ValueError(f"Duplicate Golden scenario id: {scenario_id}")
        seen_ids.add(scenario_id)
        setup = raw["setup"]
        forbidden = raw["forbidden_behavior"]
        verification = raw["verification"]
        if not isinstance(setup, dict) or not setup:
            raise ValueError(f"{scenario_id}: setup must be a non-empty mapping")
        if not _string_list(forbidden) or not _string_list(verification):
            raise ValueError(f"{scenario_id}: behavior and verification must be lists")
        expected = {
            key: value
            for key, value in raw.items()
            if key
            in {
                "expected_tool",
                "expected_citation_kind",
                "expected_memory_use",
                "expected_termination",
                "max_tool_calls",
                "mcp_tools",
                "sse_event_types",
            }
        }
        scenarios.append(
            GoldenScenario(
                id=scenario_id,
                category=_required_text(raw, "category"),
                setup=setup,
                user_input=_required_text(raw, "user_input"),
                expected_behavior=_required_text(raw, "expected_behavior"),
                forbidden_behavior=list(forbidden),
                verification=list(verification),
                expected=expected,
            )
        )
    return GoldenSet(version=1, scenarios=scenarios)


def _required_text(value: Dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"Golden scenario field {key!r} must be non-empty text")
    return item.strip()


def _string_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )

