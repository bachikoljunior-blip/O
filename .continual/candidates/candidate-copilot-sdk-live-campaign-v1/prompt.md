# Scoped Candidate: Copilot SDK live campaign migration

Scope: `agi/live-genuine-model-campaign-migration`.

Replace the known-retired GitHub Models fallback with a current GitHub Copilot SDK/CLI execution path that can run from GitHub Actions using the built-in `GITHUB_TOKEN`, while preserving the existing OpenAI API-key provider.

Activation requirements:

- preserve the current OpenAI Responses API path unchanged when `provider=openai`;
- add an explicit `provider=copilot` path using the GitHub Copilot SDK with no ambient tools and a bounded one-request session;
- use `GITHUB_TOKEN` only through the SDK's supported environment authentication and grant only `contents: read` plus `copilot-requests: write` in the live workflow;
- require explicit model selection and persist provider/model identity in campaign outputs;
- keep the existing run-count and timeout bounds, do not print credentials, and do not weaken Candidate, regression, evidence, or external-claim gates;
- keep GitHub Models rejected as negative evidence because its inference API was retired on 2026-07-30;
- add deterministic unit tests with fake SDK objects plus workflow regression assertions before scope activation;
- remain inactive globally; activate only for this exact live-campaign migration scope after exact-head CI succeeds.
