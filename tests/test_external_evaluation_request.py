from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agi.evaluation import CRITERION_KEYS, EvaluationPolicy
from agi.external_claim import DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION
from agi.external_evaluation_request import (
    ExternalEvaluationRequestError,
    build_external_evaluation_request,
    run_cli,
    validate_external_evaluation_request,
)


def _request() -> dict:
    return build_external_evaluation_request(
        repository="example/system",
        commit_sha="a" * 40,
        artifact_sha256="b" * 64,
        producer_id="system-producer",
    )


def test_strict_external_request_is_secret_free_and_bound_to_exact_policy() -> None:
    request = _request()
    result = validate_external_evaluation_request(request)

    assert result["valid"] is True
    assert request["required_criteria"] == list(CRITERION_KEYS)
    assert request["core_evaluation_policy"] == vars(EvaluationPolicy())
    assert (
        request["external_quorum"]["min_independent_groups_per_criterion"]
        == DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION
        == 2
    )
    encoded = json.dumps(request, sort_keys=True)
    for forbidden_key in (
        "bridge_secret",
        "private_key",
        "hidden_seed",
        "expected",
        "answers",
    ):
        assert f'"{forbidden_key}":' not in encoded


def test_strict_external_request_rejects_policy_weakening_and_secret_embedding() -> None:
    request = _request()

    weakened = copy.deepcopy(request)
    weakened["external_quorum"]["min_independent_groups_per_criterion"] = 1
    with pytest.raises(ExternalEvaluationRequestError, match="external_quorum"):
        validate_external_evaluation_request(weakened)

    missing_criterion = copy.deepcopy(request)
    missing_criterion["required_criteria"] = list(CRITERION_KEYS[:-1])
    with pytest.raises(ExternalEvaluationRequestError, match="required_criteria"):
        validate_external_evaluation_request(missing_criterion)

    embedded_secret = copy.deepcopy(request)
    embedded_secret["handoff"]["bridge_secret"] = "should-never-be-here"
    with pytest.raises(ExternalEvaluationRequestError, match="forbidden"):
        validate_external_evaluation_request(embedded_secret)


def test_strict_external_request_digest_detects_tampering() -> None:
    request = _request()
    request["subject"]["producer_id"] = "different-producer"
    with pytest.raises(ExternalEvaluationRequestError, match="request_sha256"):
        validate_external_evaluation_request(request)


def test_external_request_cli_create_and_validate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "request.json"
    create_code = run_cli(
        [
            "create",
            "--repository",
            "example/system",
            "--commit-sha",
            "c" * 40,
            "--artifact-sha256",
            "d" * 64,
            "--producer-id",
            "system-producer",
            "--output",
            str(output),
        ]
    )
    assert create_code == 0
    assert output.is_file()
    capsys.readouterr()

    validate_code = run_cli(["validate", str(output)])
    assert validate_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["min_independent_groups_per_criterion"] == 2
