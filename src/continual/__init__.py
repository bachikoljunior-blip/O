"""Continual, evidence-driven agent runtime."""

__all__ = ["__version__"]
__version__ = "0.2.0"

# Install the fail-closed trial guards before callers import runtime submodules. The active model may
# propose a scoped Candidate trial, but only the persistent bounded ledger and deterministic
# regression path can authorize or account for it.
from .trial_integration import install_trial_integration as _install_trial_integration

_install_trial_integration()
del _install_trial_integration
