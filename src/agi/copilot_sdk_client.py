from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

try:
    from copilot import CopilotClient
    from copilot.session import PermissionHandler
except ImportError:  # Optional except for live Copilot execution.
    CopilotClient = None  # type: ignore[assignment]
    PermissionHandler = None  # type: ignore[assignment]

_AUTH_ENV = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
_MONTHLY_QUOTA_MARKER = "exceeded your monthly quota"


class CopilotProviderBlocked(RuntimeError):
    """Stable fail-closed classification for a recognized external provider block."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Copilot provider blocked: {reason}")


def classify_copilot_provider_error(exc: BaseException) -> str | None:
    """Return a stable non-secret class for recognized provider resource failures."""
    if _MONTHLY_QUOTA_MARKER in str(exc).lower():
        return "monthly_quota_exhausted"
    return None


@dataclass(frozen=True)
class _Response:
    output_text: str


class _ResponsesProxy:
    def __init__(self, owner: "CopilotResponsesClient") -> None:
        self.owner = owner

    def create(self, *, model: str, instructions: str, input: str) -> _Response:
        return _Response(
            output_text=self.owner.respond(
                model=model,
                instructions=instructions,
                input=input,
            )
        )


class CopilotResponsesClient:
    """Minimal Responses-compatible adapter over a fresh, tool-free Copilot SDK session."""

    def __init__(
        self,
        *,
        client_factory: Callable[..., Any] | None = None,
        permission_handler: Any | None = None,
        require_auth: bool = True,
    ) -> None:
        self._github_token: str | None = None
        if client_factory is None:
            if CopilotClient is None or PermissionHandler is None:
                raise RuntimeError(
                    "github-copilot-sdk is required; install the 'copilot' extra"
                )
            self._github_token = next(
                (value for name in _AUTH_ENV if (value := os.environ.get(name))),
                None,
            )
            if require_auth and self._github_token is None:
                raise RuntimeError(
                    "Copilot execution requires COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN"
                )
            client_factory = CopilotClient
            permission_handler = PermissionHandler.approve_all
        self._client_factory = client_factory
        self._permission_handler = permission_handler
        self.responses = _ResponsesProxy(self)

    @staticmethod
    def _prompt(*, instructions: str, input: str) -> str:
        return (
            "Follow the benchmark instructions exactly. Return only the requested JSON object.\n\n"
            "<instructions>\n"
            f"{instructions.rstrip()}\n"
            "</instructions>\n\n"
            "<input>\n"
            f"{input.rstrip()}\n"
            "</input>\n"
        )

    async def _respond_async(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
    ) -> str:
        # github-copilot-sdk v1 exposes a keyword-only constructor, requires an
        # explicit per-tenant storage boundary for mode="empty", and only places
        # an auth token into its spawned runtime when github_token is supplied.
        # A temporary base directory gives every benchmark invocation a fresh,
        # isolated storage boundary that is removed after client cleanup.
        with tempfile.TemporaryDirectory(prefix="o-copilot-") as base_directory:
            client_options: dict[str, Any] = {
                "mode": "empty",
                "use_logged_in_user": False,
                "base_directory": base_directory,
            }
            if self._github_token is not None:
                client_options["github_token"] = self._github_token
            client = self._client_factory(**client_options)
            await client.start()
            try:
                session = await client.create_session(
                    model=model,
                    available_tools=[],
                    on_permission_request=self._permission_handler,
                )
                try:
                    try:
                        response = await session.send_and_wait(
                            self._prompt(instructions=instructions, input=input)
                        )
                    except Exception as exc:
                        reason = classify_copilot_provider_error(exc)
                        if reason is not None:
                            raise CopilotProviderBlocked(reason) from exc
                        raise
                finally:
                    disconnect = getattr(session, "disconnect", None)
                    if callable(disconnect):
                        await disconnect()
                content = getattr(getattr(response, "data", None), "content", None)
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError("Copilot SDK returned an empty or non-text response")
                return content
            finally:
                await client.stop()

    def respond(self, *, model: str, instructions: str, input: str) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self._respond_async(
                    model=model,
                    instructions=instructions,
                    input=input,
                )
            )
        raise RuntimeError(
            "CopilotResponsesClient synchronous adapter cannot run inside an active asyncio loop"
        )
