from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


PRE_APPLICATION_DECISIONS = {
    "NOT_APPLICABLE",
    "USE_ACTIVE",
    "TRIAL_CANDIDATE",
    "USE_CANDIDATE",
    "ACTIVE_FOR_SCOPE",
    "BASELINE_FIRST",
    "DEFER",
    "BLOCKED",
}
POST_RESULT_DECISIONS = {
    "VERIFIED_FOR_SCOPE",
    "ACTIVE_FOR_SCOPE",
    "REJECTED_FOR_SCOPE",
    "REMAIN_CANDIDATE",
    "NEED_MORE_EVIDENCE",
    "UNCERTAIN",
}
TASK_VERDICTS = {"PASS", "FAIL", "UNCERTAIN"}
ROOT_COMPONENTS = {"execute", "task_evaluate", "consolidate_episode", "learn"}


@dataclass(frozen=True)
class ContractError(ValueError):
    component: str
    errors: tuple[str, ...]

    def __str__(self) -> str:
        return f"invalid {self.component} output: " + "; ".join(self.errors)


def _require_dict(value: Any, name: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return {}
    return value


def _candidate_lists(result: dict[str, Any]) -> Iterable[Any]:
    for key in ("candidates", "candidate_proposals"):
        value = result.get(key)
        if isinstance(value, dict):
            yield value
        elif isinstance(value, list):
            yield from value


def validate_component_output(
    component: str,
    output: Any,
    *,
    evaluator_mode: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    body = _require_dict(output, "output", errors)
    result = _require_dict(body.get("result"), "result", errors)
    _require_dict(body.get("fragment"), "fragment", errors)

    if component == "learn":
        if "local_learn" in body:
            errors.append("Learn must not recursively return local_learn")
    else:
        _require_dict(body.get("local_learn"), "local_learn", errors)

    if component == "root":
        selected = result.get("component")
        if selected not in ROOT_COMPONENTS:
            errors.append(f"root result.component must be one of {sorted(ROOT_COMPONENTS)}")
        if not isinstance(result.get("goal"), str) or not result.get("goal", "").strip():
            errors.append("root result.goal must be a non-empty string")

    if component == "task_evaluate":
        verdict = result.get("verdict") or result.get("status")
        if verdict not in TASK_VERDICTS:
            errors.append(f"task_evaluate verdict must be one of {sorted(TASK_VERDICTS)}")

    if component == "candidate_evaluate":
        mode = evaluator_mode or "pre-application"
        decision = result.get("decision") or result.get("scope_state")
        allowed = PRE_APPLICATION_DECISIONS if mode == "pre-application" else POST_RESULT_DECISIONS
        if decision not in allowed:
            errors.append(f"candidate_evaluate decision for {mode} must be one of {sorted(allowed)}")
        if decision in {"TRIAL_CANDIDATE", "USE_CANDIDATE", "ACTIVE_FOR_SCOPE"}:
            candidate_id = result.get("candidate_id") or result.get("selected_candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                errors.append("candidate selection requires candidate_id")

    for proposal in _candidate_lists(result):
        if not isinstance(proposal, dict):
            errors.append("candidate proposal must be an object")
            continue
        target = proposal.get("target_component")
        if not isinstance(target, str) or not target:
            errors.append("candidate proposal requires target_component")
        prompt_content = proposal.get("prompt_content")
        prompt_path = proposal.get("prompt_path")
        prompt_mode = proposal.get("prompt_mode", "overlay")
        if prompt_content is not None and not isinstance(prompt_content, str):
            errors.append("candidate prompt_content must be a string")
        if prompt_path is not None and not isinstance(prompt_path, str):
            errors.append("candidate prompt_path must be a string")
        if prompt_mode not in {"overlay", "replace"}:
            errors.append("candidate prompt_mode must be overlay or replace")

    if errors:
        raise ContractError(component, tuple(errors))
    return body
