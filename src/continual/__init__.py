"""Continual, evidence-driven agent runtime."""

__all__ = ["__version__"]
__version__ = "0.2.0"

# Install the fail-closed trial guards before callers import runtime submodules. The active model may
# propose a scoped Candidate trial, but only the persistent bounded ledger and deterministic
# regression path can authorize or account for it.
from . import candidate_regression as _candidate_regression
from .trial_integration import (
    bounded_record_candidate_regression as _bounded_record_candidate_regression,
    install_trial_integration as _install_trial_integration,
)

_install_trial_integration()


def _public_record_candidate_regression(*args, **kwargs):
    candidate_id = kwargs.get("candidate_id")
    if not isinstance(candidate_id, str) or not _candidate_regression._SAFE_ID.fullmatch(candidate_id):
        raise ValueError("candidate_id must contain only letters, digits, '.', '_' or '-'")
    return _bounded_record_candidate_regression(*args, **kwargs)


_candidate_regression.record_candidate_regression = _public_record_candidate_regression

# The historical contracted-external-tool module remains readable for prior internal evidence, but
# the ordinary LearningEnabledEngine constructor is replaced with the fail-closed runner-attested
# implementation. This keeps direct legacy harness reproduction separate from the active runtime path.
from . import learning_engine as _learning_engine
from .attested_learning_engine import LearningEnabledEngine as _attested_learning_engine

_learning_engine.LearningEnabledEngine = _attested_learning_engine

# Task-chat self-application is a public persistence boundary. Install strict boolean and native-path
# collision guards before callers import the recorder so malformed context flags or reused Run,
# Episode, and evidence identifiers fail before any durable mutation.
from .self_application_guard import install_self_application_guard as _install_self_application_guard

_install_self_application_guard()

del _install_trial_integration
del _install_self_application_guard
