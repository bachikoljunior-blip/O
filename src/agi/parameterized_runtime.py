from __future__ import annotations

from typing import Any, Mapping

from .induction import InductionError
from .parameterized_tools import _safe_apply, _validate_parameters


def validate_parameterized_tool(domain: str, family: str, parameters: Mapping[str, Any]) -> None:
    """Validate a parameterized tool against the audited safe-family contract."""

    _validate_parameters(domain, family, parameters)


def apply_parameterized_tool(
    domain: str,
    family: str,
    parameters: Mapping[str, Any],
    value: Any,
) -> Any:
    """Execute only a validated audited parameterized family, never arbitrary source code."""

    try:
        _validate_parameters(domain, family, parameters)
        return _safe_apply(domain, family, parameters, value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InductionError("parameterized tool input is invalid for its audited family") from exc
