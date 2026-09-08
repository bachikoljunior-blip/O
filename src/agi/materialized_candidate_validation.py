from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from continual.candidate_regression import CandidateIntegrityError, candidate_verified_for_scope


def validate_materialized_candidates(root: Path) -> dict[str, Any]:
    """Validate durable Candidate state exactly as a fresh checkout would consume it.

    Materialization is not successful merely because evidence files were committed. Runtime
    registries read `.continual/candidates/*/candidate.json`, and exact-scope activation also needs
    the referenced regression decision records. This validator therefore fails closed when no
    candidate state is present, when an active Candidate lacks verified scopes, or when any persisted
    verification binding cannot be independently reloaded from the supplied repository root.
    Rejected/inactive Candidates are allowed so negative evidence can remain durable.
    """

    root = root.resolve()
    candidate_paths = sorted((root / ".continual" / "candidates").glob("*/candidate.json"))
    if not candidate_paths:
        raise RuntimeError("materialized repository contains no Candidate state")

    candidate_ids: list[str] = []
    verified_bindings: list[dict[str, str]] = []
    inactive_candidate_ids: list[str] = []

    for path in candidate_paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"materialized Candidate must be an object: {path}")
        candidate_id = str(raw.get("candidate_id", ""))
        if not candidate_id or path.parent.name != candidate_id:
            raise RuntimeError(f"materialized Candidate identity mismatch: {path}")
        candidate_ids.append(candidate_id)

        verified_states = raw.get("verified_scope_states")
        if verified_states is None:
            verified_states = {}
        if not isinstance(verified_states, Mapping):
            raise RuntimeError(f"verified_scope_states must be an object: {candidate_id}")

        verified_scopes = sorted(str(scope) for scope in verified_states)
        if raw.get("status") == "active-for-scope" and not verified_scopes:
            raise RuntimeError(f"active Candidate has no verified scopes: {candidate_id}")

        if not verified_scopes:
            inactive_candidate_ids.append(candidate_id)
            continue

        for scope in verified_scopes:
            try:
                verified = candidate_verified_for_scope(root, raw, scope)
            except CandidateIntegrityError as exc:
                raise RuntimeError(
                    f"materialized Candidate verification binding is invalid: {candidate_id}:{scope}: {exc}"
                ) from exc
            if not verified:
                raise RuntimeError(
                    f"materialized Candidate is not independently verified for scope: {candidate_id}:{scope}"
                )
            verified_bindings.append({"candidate_id": candidate_id, "scope": scope})

    if not verified_bindings:
        raise RuntimeError("materialized repository contains no regression-verified Candidate capability")

    return {
        "schema_version": 1,
        "candidate_count": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "verified_binding_count": len(verified_bindings),
        "verified_bindings": verified_bindings,
        "inactive_candidate_count": len(inactive_candidate_ids),
        "inactive_candidate_ids": inactive_candidate_ids,
        "passed": True,
        "claim_boundary": (
            "This validates durable repository Candidate state and exact regression bindings after "
            "checkout. It is internal development evidence, not independent production AGI proof."
        ),
    }


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate materialized Candidate state in a checkout")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    report = validate_materialized_candidates(Path(args.root))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
