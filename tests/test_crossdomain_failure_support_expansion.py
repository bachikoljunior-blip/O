from __future__ import annotations

from agi.crossdomain_failure_derived_target import _expand_support_inputs


def test_support_expansion_keeps_all_bounded_persisted_derived_inputs() -> None:
    base_program = {
        "input_domain": "string",
        "output_domain": "string",
        "expression": {"op": "input"},
    }

    expanded = _expand_support_inputs(
        base_program,
        ("ab", "cd"),
        history_digest="0" * 64,
    )

    # The retained counterexample was caused by the old arbitrary total-support cap of eight.
    # Two persisted inputs plus the eight distinct bounded deterministic string derivations
    # must all survive into the support set; no sealed held-out answers participate here.
    assert expanded[:2] == ("ab", "cd")
    assert len(expanded) == 10
    assert len({repr(value) for value in expanded}) == len(expanded)
