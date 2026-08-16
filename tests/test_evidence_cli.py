from __future__ import annotations

import pytest

from agi.evidence_cli import load_trusted_attestors


def test_trusted_attestors_load_only_from_explicit_environment():
    value = load_trusted_attestors({"AGI_TRUSTED_ATTESTORS_JSON": '{"lab-a":"secret-a"}'})
    assert value == {"lab-a": "secret-a"}


def test_missing_trust_configuration_fails_closed_to_empty_registry():
    assert load_trusted_attestors({}) == {}


def test_invalid_trust_configuration_is_rejected():
    with pytest.raises(ValueError):
        load_trusted_attestors({"AGI_TRUSTED_ATTESTORS_JSON": "[]"})
    with pytest.raises(ValueError):
        load_trusted_attestors({"AGI_TRUSTED_ATTESTORS_JSON": '{"lab":""}'})
