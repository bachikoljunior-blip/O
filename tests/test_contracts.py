from __future__ import annotations

import pytest

from continual.contracts import ContractError, validate_component_output


def output(result: dict, *, local: bool = True) -> dict:
    value = {"result": result, "fragment": {"component": "test"}}
    if local:
        value["local_learn"] = {"decision": "NO_CHANGE", "candidates": []}
    return value


def test_root_requires_one_valid_component_and_goal():
    validate_component_output("root", output({"component": "execute", "goal": "make artifact"}))
    with pytest.raises(ContractError):
        validate_component_output("root", output({"component": "unknown", "goal": ""}))


def test_task_evaluator_cannot_invent_verdict():
    with pytest.raises(ContractError):
        validate_component_output("task_evaluate", output({"verdict": "MAYBE"}))


def test_learn_cannot_recursively_local_learn():
    with pytest.raises(ContractError):
        validate_component_output(
            "learn",
            {
                "result": {"decision": "NO_CHANGE", "candidates": []},
                "fragment": {},
                "local_learn": {},
            },
        )


def test_candidate_selection_requires_candidate_id():
    with pytest.raises(ContractError):
        validate_component_output(
            "candidate_evaluate",
            output({"decision": "TRIAL_CANDIDATE"}),
            evaluator_mode="pre-application",
        )


def test_post_result_can_propose_but_cannot_self_promote_candidate():
    validate_component_output(
        "candidate_evaluate",
        output({"decision": "PROPOSE_ACTIVATION", "candidate_id": "candidate-a"}),
        evaluator_mode="post-result",
    )
    for forbidden in ("ACTIVE_FOR_SCOPE", "VERIFIED_FOR_SCOPE"):
        with pytest.raises(ContractError):
            validate_component_output(
                "candidate_evaluate",
                output({"decision": forbidden, "candidate_id": "candidate-a"}),
                evaluator_mode="post-result",
            )
