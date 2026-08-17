from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .attested_external_tools import (
    AttestedContractedExternalToolRegistry,
    ExternalToolRunner,
    PinnedRunnerAttestationVerifier,
)
from .contracted_external_tools import ContractedExternalToolError, ExternalToolAdapter
from .engine import Engine
from .learning_engine import LearningEnabledEngine as _LegacyLearningEnabledEngine


class LearningEnabledEngine(_LegacyLearningEnabledEngine):
    """LearningEnabledEngine whose active external-tool path fails closed on in-process callables.

    Legacy ExternalToolAdapter values remain importable so historical internal harness evidence can be
    read and reproduced explicitly, but the ordinary engine constructor never activates them. A host
    capability must instead arrive as an ExternalToolRunner plus a separately configured attestation
    verifier. Inherited learned-tool/acquired-program behavior is unchanged.
    """

    def __init__(
        self,
        root: Path,
        *,
        external_tool_adapters: Mapping[str, ExternalToolAdapter] | None = None,
        external_tool_runners: Mapping[str, ExternalToolRunner] | None = None,
        external_runner_verifier: PinnedRunnerAttestationVerifier | None = None,
    ) -> None:
        if external_tool_adapters:
            raise ContractedExternalToolError(
                "arbitrary in-process external_tool_adapters are quarantined; supply an "
                "ExternalToolRunner and a separately configured isolation attestation verifier"
            )
        Engine.__init__(self, root)
        self._external_tool_registry = AttestedContractedExternalToolRegistry(
            self.root,
            external_tool_runners or {},
            external_runner_verifier,
        )
