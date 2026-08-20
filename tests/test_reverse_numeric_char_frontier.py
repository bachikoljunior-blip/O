from __future__ import annotations

from agi.crossdomain_failure_derived_target import _precommit_schedule


def test_reverse_numeric_char_fallback_preserves_wrapper_and_bounds() -> None:
    base_program = {
        "input_domain": "sequence",
        "output_domain": "sequence",
        "expression": {
            "op": "chars",
            "arg": {
                "op": "reverse",
                "arg": {
                    "op": "to_string",
                    "arg": {
                        "op": "add",
                        "left": {"op": "const", "domain": "numeric", "value": 1},
                        "right": {
                            "op": "mul",
                            "left": {"op": "const", "domain": "numeric", "value": -3},
                            "right": {
                                "op": "fold_numeric",
                                "arg": {"op": "input"},
                                "reducer": "add",
                                "initial": 0,
                            },
                        },
                    },
                },
            },
        },
        "effects": [],
        "max_steps": 64,
        "max_output_length": 1024,
    }
    support_inputs = (
        [],
        [1, -1],
        [2, -3, 1],
        [1],
        [-8, 0, 0],
        [-7, 0, 0],
        [10, 0, 0],
    )

    schedule = _precommit_schedule(
        base_program,
        history_digest="0" * 64,
        support_inputs=support_inputs,
    )

    targets = schedule["target_schedule"]
    assert len(targets) >= 2
    assert len({target["semantic_signature"] for target in targets}) == len(targets)
    for target in targets:
        expression = target["program"]["expression"]
        assert expression["op"] == "chars"
        assert expression["arg"]["op"] == "reverse"
        assert expression["arg"]["arg"]["op"] == "to_string"
        assert target["program_nodes"] == 9
        assert target["program"]["max_steps"] == 64
        assert target["program"]["max_output_length"] == 1024
