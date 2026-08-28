from __future__ import annotations

import hashlib
import json
from collections import deque
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class RecursiveContextAblationError(ValueError):
    """Raised when the bounded retrieval ablation cannot remain fail-closed."""


EXPERIMENT_ID = "recursive-context-retrieval-ablation-v1"
MECHANISM = "bounded-deterministic-two-hop-recursive-context-retrieval-ablation"
FIXTURE_IDS = (
    "missing-transitive-dependency",
    "stale-competing-source",
    "authority-conflicting-source",
)

_TOP_FIELDS = {
    "schema_version",
    "experiment_id",
    "base_commit",
    "decision_at",
    "max_depth",
    "fixtures",
}
_FIXTURE_FIELDS = {
    "fixture_id",
    "situation",
    "entry_source_ids",
    "required_source_ids",
    "authority_bindings",
    "active_invalidations",
    "sources",
}
_SOURCE_FIELDS = {
    "source_id",
    "repository_path",
    "content_sha256",
    "summary",
    "dependency_ids",
    "authority_scope",
    "authority_id",
    "observed_at",
    "valid_until",
    "invalidates_on",
}
_HEX = set("0123456789abcdef")


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecursiveContextAblationError(f"{label} must be non-empty text")
    return value


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise RecursiveContextAblationError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _timestamp(value: Any, label: str) -> datetime:
    text = _nonempty(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecursiveContextAblationError(
            f"{label} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise RecursiveContextAblationError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _unique_text_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        suffix = " non-empty" if nonempty else ""
        raise RecursiveContextAblationError(f"{label} must be a{suffix} list")
    result = [_nonempty(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise RecursiveContextAblationError(f"{label} must not contain duplicates")
    return result


def _repository_file(root: Path, raw_path: Any, label: str) -> tuple[str, Path]:
    text = _nonempty(raw_path, label)
    if "\\" in text:
        raise RecursiveContextAblationError(
            f"{label} must use POSIX repository-relative separators"
        )
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise RecursiveContextAblationError(
            f"{label} must be a confined repository-relative path"
        )
    candidate = (root / Path(*pure.parts)).resolve()
    if candidate == root or root not in candidate.parents:
        raise RecursiveContextAblationError(f"{label} escapes repository root")
    if candidate.is_symlink() or not candidate.is_file():
        raise RecursiveContextAblationError(
            f"{label} must name a regular repository file"
        )
    return pure.as_posix(), candidate


def _assert_acyclic(sources: Mapping[str, Mapping[str, Any]], label: str) -> None:
    active: set[str] = set()
    complete: set[str] = set()

    def visit(source_id: str) -> None:
        if source_id in complete:
            return
        if source_id in active:
            raise RecursiveContextAblationError(f"{label} dependency graph has a cycle")
        active.add(source_id)
        for dependency_id in sources[source_id]["dependency_ids"]:
            visit(dependency_id)
        active.remove(source_id)
        complete.add(source_id)

    for source_id in sorted(sources):
        visit(source_id)


def validate_recursive_context_retrieval_fixtures(
    value: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    """Validate three immutable repository-backed fixtures before measurement."""

    root = root.resolve()
    if not isinstance(value, Mapping) or set(value) != _TOP_FIELDS:
        raise RecursiveContextAblationError("fixture manifest has an unexpected schema")
    if value.get("schema_version") != 1:
        raise RecursiveContextAblationError("fixture schema_version must equal 1")
    if value.get("experiment_id") != EXPERIMENT_ID:
        raise RecursiveContextAblationError("fixture experiment_id changed")
    base_commit = value.get("base_commit")
    if (
        not isinstance(base_commit, str)
        or len(base_commit) != 40
        or any(character not in _HEX for character in base_commit)
    ):
        raise RecursiveContextAblationError("base_commit must be a lowercase Git SHA")
    decision_at = _timestamp(value.get("decision_at"), "decision_at")
    if value.get("max_depth") != 2:
        raise RecursiveContextAblationError("the ablation max_depth must remain exactly 2")
    raw_fixtures = value.get("fixtures")
    if not isinstance(raw_fixtures, list) or len(raw_fixtures) != 3:
        raise RecursiveContextAblationError("exactly three fixtures are required")

    fixture_ids: list[str] = []
    validated_fixtures: list[dict[str, Any]] = []
    for fixture_index, raw_fixture in enumerate(raw_fixtures):
        label = f"fixtures[{fixture_index}]"
        if not isinstance(raw_fixture, Mapping) or set(raw_fixture) != _FIXTURE_FIELDS:
            raise RecursiveContextAblationError(f"{label} has an unexpected schema")
        fixture = deepcopy(dict(raw_fixture))
        fixture_id = _nonempty(fixture.get("fixture_id"), f"{label}.fixture_id")
        fixture_ids.append(fixture_id)
        _nonempty(fixture.get("situation"), f"{label}.situation")
        entry_ids = _unique_text_list(
            fixture.get("entry_source_ids"),
            f"{label}.entry_source_ids",
            nonempty=True,
        )
        required_ids = _unique_text_list(
            fixture.get("required_source_ids"),
            f"{label}.required_source_ids",
            nonempty=True,
        )
        invalidations = _unique_text_list(
            fixture.get("active_invalidations"),
            f"{label}.active_invalidations",
        )
        bindings = fixture.get("authority_bindings")
        if not isinstance(bindings, Mapping) or not bindings:
            raise RecursiveContextAblationError(
                f"{label}.authority_bindings must be a non-empty object"
            )
        exact_bindings = {
            _nonempty(scope, f"{label}.authority_bindings scope"): _nonempty(
                authority_id,
                f"{label}.authority_bindings[{scope}]",
            )
            for scope, authority_id in bindings.items()
        }

        raw_sources = fixture.get("sources")
        if not isinstance(raw_sources, list) or len(raw_sources) < 3:
            raise RecursiveContextAblationError(
                f"{label}.sources must contain at least three records"
            )
        sources: dict[str, dict[str, Any]] = {}
        repository_paths: set[str] = set()
        for source_index, raw_source in enumerate(raw_sources):
            source_label = f"{label}.sources[{source_index}]"
            if not isinstance(raw_source, Mapping) or set(raw_source) != _SOURCE_FIELDS:
                raise RecursiveContextAblationError(
                    f"{source_label} has an unexpected schema"
                )
            source = deepcopy(dict(raw_source))
            source_id = _nonempty(source.get("source_id"), f"{source_label}.source_id")
            if source_id in sources:
                raise RecursiveContextAblationError(f"{label} source IDs must be unique")
            repository_path, path = _repository_file(
                root,
                source.get("repository_path"),
                f"{source_label}.repository_path",
            )
            if repository_path in repository_paths:
                raise RecursiveContextAblationError(
                    f"{label} repository paths must be unique"
                )
            repository_paths.add(repository_path)
            expected_digest = _sha256(
                source.get("content_sha256"),
                f"{source_label}.content_sha256",
            )
            observed_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if observed_digest != expected_digest:
                raise RecursiveContextAblationError(
                    f"repository content digest mismatch: {repository_path}"
                )
            _nonempty(source.get("summary"), f"{source_label}.summary")
            source["dependency_ids"] = _unique_text_list(
                source.get("dependency_ids"),
                f"{source_label}.dependency_ids",
            )
            scope = _nonempty(
                source.get("authority_scope"),
                f"{source_label}.authority_scope",
            )
            _nonempty(source.get("authority_id"), f"{source_label}.authority_id")
            if scope not in exact_bindings:
                raise RecursiveContextAblationError(
                    f"{source_label} has no authority binding"
                )
            observed_at = _timestamp(
                source.get("observed_at"), f"{source_label}.observed_at"
            )
            valid_until = _timestamp(
                source.get("valid_until"), f"{source_label}.valid_until"
            )
            if valid_until < observed_at:
                raise RecursiveContextAblationError(
                    f"{source_label}.valid_until predates observed_at"
                )
            source["invalidates_on"] = _unique_text_list(
                source.get("invalidates_on"),
                f"{source_label}.invalidates_on",
                nonempty=True,
            )
            source["repository_path"] = repository_path
            source["content_bytes"] = path.stat().st_size
            sources[source_id] = source

        source_ids = set(sources)
        if not set(entry_ids) <= source_ids:
            raise RecursiveContextAblationError(f"{label} has an unknown entry source")
        if not set(required_ids) <= source_ids:
            raise RecursiveContextAblationError(f"{label} has an unknown required source")
        for source in sources.values():
            dependencies = set(source["dependency_ids"])
            if source["source_id"] in dependencies or not dependencies <= source_ids:
                raise RecursiveContextAblationError(
                    f"{label} has an invalid dependency reference"
                )
        _assert_acyclic(sources, label)

        reachable_depth: dict[str, int] = {}
        pending = deque((source_id, 0) for source_id in sorted(entry_ids))
        while pending:
            source_id, depth = pending.popleft()
            prior = reachable_depth.get(source_id)
            if prior is not None and prior <= depth:
                continue
            reachable_depth[source_id] = depth
            if depth < 2:
                pending.extend(
                    (dependency_id, depth + 1)
                    for dependency_id in sorted(sources[source_id]["dependency_ids"])
                )
        if not set(required_ids) <= reachable_depth.keys():
            raise RecursiveContextAblationError(
                f"{label} required sources are not reachable within two hops"
            )
        if 2 not in {reachable_depth[source_id] for source_id in required_ids}:
            raise RecursiveContextAblationError(
                f"{label} must require at least one exact depth-two source"
            )

        fixture["entry_source_ids"] = entry_ids
        fixture["required_source_ids"] = required_ids
        fixture["authority_bindings"] = exact_bindings
        fixture["active_invalidations"] = invalidations
        fixture["sources"] = [sources[source_id] for source_id in sorted(sources)]
        fixture["decision_at"] = decision_at.isoformat().replace("+00:00", "Z")
        validated_fixtures.append(fixture)

    if tuple(fixture_ids) != FIXTURE_IDS:
        raise RecursiveContextAblationError(
            "fixture IDs and order must equal the frozen three-case contract"
        )
    validated = deepcopy(dict(value))
    validated["fixtures"] = validated_fixtures
    return validated


def load_recursive_context_retrieval_fixtures(
    path: Path,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    root = (root or path.resolve().parents[1]).resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecursiveContextAblationError(
            "fixture manifest must be readable UTF-8 JSON"
        ) from exc
    if not isinstance(value, Mapping):
        raise RecursiveContextAblationError("fixture manifest must be an object")
    return validate_recursive_context_retrieval_fixtures(value, root=root)


def _guard_reasons(source: Mapping[str, Any], fixture: Mapping[str, Any]) -> list[str]:
    decision_at = _timestamp(fixture["decision_at"], "fixture.decision_at")
    observed_at = _timestamp(source["observed_at"], "source.observed_at")
    valid_until = _timestamp(source["valid_until"], "source.valid_until")
    reasons: list[str] = []
    if observed_at > decision_at:
        reasons.append("future_observation")
    if valid_until < decision_at:
        reasons.append("stale_at_decision_time")
    invalidations = sorted(
        set(source["invalidates_on"]) & set(fixture["active_invalidations"])
    )
    reasons.extend(f"invalidated:{item}" for item in invalidations)
    expected_authority = fixture["authority_bindings"][source["authority_scope"]]
    if source["authority_id"] != expected_authority:
        reasons.append(
            f"authority_conflict:{source['authority_scope']}->{expected_authority}"
        )
    return reasons


def _selection_result(
    fixture: Mapping[str, Any],
    *,
    method: str,
) -> dict[str, Any]:
    sources = {source["source_id"]: source for source in fixture["sources"]}
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reached: set[str] = set()

    if method == "flat":
        queue = deque(
            (source_id, 0, None)
            for source_id in sorted(fixture["entry_source_ids"])
        )
        unreached_reason = "not_reached_by_flat_selector"
    elif method == "recursive_two_hop":
        queue = deque(
            (source_id, 0, None)
            for source_id in sorted(fixture["entry_source_ids"])
        )
        unreached_reason = "not_reached_within_two_hops"
    else:
        raise RecursiveContextAblationError(f"unknown retrieval method: {method}")

    scheduled = set(fixture["entry_source_ids"])
    while queue:
        source_id, depth, parent_source_id = queue.popleft()
        reached.add(source_id)
        source = sources[source_id]
        reasons = _guard_reasons(source, fixture)
        if reasons:
            rejected.append(
                {
                    "source_id": source_id,
                    "repository_path": source["repository_path"],
                    "reasons": reasons,
                }
            )
            continue
        selected.append(
            {
                "source_id": source_id,
                "repository_path": source["repository_path"],
                "content_sha256": source["content_sha256"],
                "content_bytes": source["content_bytes"],
                "depth": depth,
                "parent_source_id": parent_source_id,
                "selection_reason": (
                    "entry_source" if depth == 0 else "declared_dependency"
                ),
            }
        )
        if method == "recursive_two_hop" and depth < 2:
            for dependency_id in sorted(source["dependency_ids"]):
                if dependency_id not in scheduled:
                    scheduled.add(dependency_id)
                    queue.append((dependency_id, depth + 1, source_id))

    for source_id in sorted(set(sources) - reached):
        source = sources[source_id]
        rejected.append(
            {
                "source_id": source_id,
                "repository_path": source["repository_path"],
                "reasons": [unreached_reason],
            }
        )
    selected.sort(key=lambda item: (item["depth"], item["source_id"]))
    rejected.sort(key=lambda item: item["source_id"])
    selected_ids = {item["source_id"] for item in selected}
    required_ids = set(fixture["required_source_ids"])
    recovered = sorted(selected_ids & required_ids)
    missing = sorted(required_ids - selected_ids)
    unsafe = sorted(selected_ids - required_ids)
    result = {
        "method": method,
        "selected_sources": selected,
        "rejected_sources": rejected,
        "required_source_ids": sorted(required_ids),
        "recovered_required_source_ids": recovered,
        "missing_required_source_ids": missing,
        "required_source_recall": len(recovered) / len(required_ids),
        "unsafe_admitted_source_ids": unsafe,
        "unsafe_admission_count": len(unsafe),
        "selected_source_count": len(selected),
        "selected_content_bytes": sum(item["content_bytes"] for item in selected),
    }
    result["manifest_digest"] = _canonical_digest(result)
    return result


def _has_rejection(result: Mapping[str, Any], source_id: str, reason: str) -> bool:
    return any(
        item["source_id"] == source_id and reason in item["reasons"]
        for item in result["rejected_sources"]
    )


def run_recursive_context_retrieval_ablation(
    root: Path,
    *,
    fixture_path: Path | None = None,
) -> dict[str, Any]:
    """Run the frozen flat-versus-two-hop comparison without production activation."""

    root = root.resolve()
    fixture_path = fixture_path or root / "agi" / "RECURSIVE_CONTEXT_RETRIEVAL_FIXTURES.json"
    manifest = load_recursive_context_retrieval_fixtures(fixture_path, root=root)
    fixture_results: list[dict[str, Any]] = []
    for fixture in manifest["fixtures"]:
        baseline = _selection_result(fixture, method="flat")
        recursive = _selection_result(fixture, method="recursive_two_hop")
        replay = _selection_result(fixture, method="recursive_two_hop")
        deterministic = recursive == replay
        fixture_id = fixture["fixture_id"]
        guard_verified = True
        if fixture_id == "missing-transitive-dependency":
            guard_verified = any(
                item["source_id"] == "work-source-observation-contract"
                and item["depth"] == 2
                for item in recursive["selected_sources"]
            )
        elif fixture_id == "stale-competing-source":
            guard_verified = _has_rejection(
                recursive,
                "stale-ci-observation",
                "stale_at_decision_time",
            )
        elif fixture_id == "authority-conflicting-source":
            guard_verified = _has_rejection(
                recursive,
                "legacy-autonomy-state",
                "authority_conflict:work-state->o-work-mode-monitor",
            )
        passed = (
            deterministic
            and recursive["required_source_recall"] == 1.0
            and recursive["required_source_recall"] > baseline["required_source_recall"]
            and recursive["unsafe_admission_count"] == 0
            and guard_verified
        )
        fixture_results.append(
            {
                "fixture_id": fixture_id,
                "baseline": baseline,
                "recursive": recursive,
                "recall_delta": (
                    recursive["required_source_recall"]
                    - baseline["required_source_recall"]
                ),
                "unsafe_admission_delta": (
                    recursive["unsafe_admission_count"]
                    - baseline["unsafe_admission_count"]
                ),
                "deterministic_replays": 2,
                "deterministic_replay_verified": deterministic,
                "guard_verified": guard_verified,
                "verdict": "PASS" if passed else "FAIL",
            }
        )

    all_passed = all(item["verdict"] == "PASS" for item in fixture_results)
    baseline_mean = sum(
        item["baseline"]["required_source_recall"] for item in fixture_results
    ) / len(fixture_results)
    recursive_mean = sum(
        item["recursive"]["required_source_recall"] for item in fixture_results
    ) / len(fixture_results)
    stale_rejections = sum(
        1
        for item in fixture_results
        for rejection in item["recursive"]["rejected_sources"]
        if "stale_at_decision_time" in rejection["reasons"]
    )
    authority_rejections = sum(
        1
        for item in fixture_results
        for rejection in item["recursive"]["rejected_sources"]
        if any(reason.startswith("authority_conflict:") for reason in rejection["reasons"])
    )
    report = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "mechanism": MECHANISM,
        "status": "MEASURED",
        "base_commit": manifest["base_commit"],
        "fixture_manifest_digest": _canonical_digest(
            {
                key: value
                for key, value in manifest.items()
                if key != "fixtures"
            }
            | {
                "fixtures": [
                    {
                        key: value
                        for key, value in fixture.items()
                        if key != "decision_at"
                    }
                    for fixture in manifest["fixtures"]
                ]
            }
        ),
        "production_activation": False,
        "fixtures": fixture_results,
        "summary": {
            "fixture_count": len(fixture_results),
            "baseline_mean_required_source_recall": baseline_mean,
            "recursive_mean_required_source_recall": recursive_mean,
            "mean_recall_delta": recursive_mean - baseline_mean,
            "recursive_unsafe_admission_count": sum(
                item["recursive"]["unsafe_admission_count"]
                for item in fixture_results
            ),
            "stale_source_rejection_count": stale_rejections,
            "authority_conflict_rejection_count": authority_rejections,
            "deterministic_replay_count": sum(
                item["deterministic_replays"] for item in fixture_results
            ),
        },
        "decision": {
            "verdict": "PASS" if all_passed else "FAIL",
            "reason": (
                "The bounded two-hop variant recovered every declared transitive source "
                "that the flat selector missed across all three fixtures, while admitting "
                "zero non-required sources and rejecting the exact stale and authority-"
                "conflicting competitors."
                if all_passed
                else "At least one frozen fixture failed recall, deterministic replay, or a fail-closed guard."
            ),
        },
        "claim_boundary": {
            "scope": "three frozen repository fixtures at base commit 523f171; non-production ablation only",
            "current_context_kernel_modified": False,
            "production_routing_activated": False,
            "candidate_activated": False,
            "external_independent_evidence": False,
            "agi_claim_supported": False,
            "user_goal_completed": False,
        },
    }
    report["report_digest"] = _canonical_digest(report)
    return report


def validate_recursive_context_retrieval_report(
    value: Mapping[str, Any],
    *,
    root: Path,
    fixture_path: Path | None = None,
) -> dict[str, Any]:
    """Require a checked-in report to equal a fresh deterministic recomputation."""

    if not isinstance(value, Mapping):
        raise RecursiveContextAblationError("ablation report must be an object")
    exact = deepcopy(dict(value))
    supplied = exact.pop("report_digest", None)
    if supplied != _canonical_digest(exact):
        raise RecursiveContextAblationError("ablation report digest mismatch")
    exact["report_digest"] = supplied
    recomputed = run_recursive_context_retrieval_ablation(
        root,
        fixture_path=fixture_path,
    )
    if exact != recomputed:
        raise RecursiveContextAblationError(
            "checked-in ablation report differs from deterministic recomputation"
        )
    return exact


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    print(
        json.dumps(
            run_recursive_context_retrieval_ablation(root),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
