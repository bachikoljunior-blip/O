from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable

try:
    from copilot import CopilotClient
    from copilot.session import PermissionHandler
except ImportError:  # Optional except for live Copilot execution.
    CopilotClient = None  # type: ignore[assignment]
    PermissionHandler = None  # type: ignore[assignment]

_AUTH_ENV = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


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
        if client_factory is None:
            if CopilotClient is None or PermissionHandler is None:
                raise RuntimeError(
                    "github-copilot-sdk is required; install the 'copilot' extra"
                )
            if require_auth and not any(os.environ.get(name) for name in _AUTH_ENV):
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
        # github-copilot-sdk v1 exposes a keyword-only CopilotClient constructor.
        # Keep the injected factory on the same contract so tests cannot accidentally
        # validate a positional call that the production SDK rejects.
        client = self._client_factory(
            mode="empty",
            use_logged_in_user=False,
        )
        await client.start()
        try:
            session = await client.create_session(
                model=model,
                available_tools=[],
                on_permission_request=self._permission_handler,
            )
            try:
                response = await session.send_and_wait(
                    self._prompt(instructions=instructions, input=input)
                )
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
