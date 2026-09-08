# Scoped Candidate: secret-free GitHub Models live campaign fallback

Scope: `agi/live-genuine-model-campaign-migration`.

When the exact task is enabling a longer genuine-model continual campaign, preserve the existing OpenAI API-key path but add a fail-closed GitHub Models fallback using the ephemeral GitHub Actions token with `models: read`. Keep provider/model identity explicit, retain the existing bounded run count and timeout, never print credential material, and do not weaken Candidate, regression, evidence, or external-claim gates.

Activation requirements:

- the existing OpenAI-key path remains preferred and behaviorally unchanged when configured;
- GitHub Models is used only when the OpenAI key is absent;
- the workflow grants only `contents: read` and `models: read` needed for the fallback;
- the GitHub token is passed only through environment to the existing sanitized model client;
- model identifiers for OpenAI and GitHub Models are distinct inputs so neither provider receives an invalid alias;
- repository CI adds regression assertions for the workflow and existing provider-fallback unit tests continue to pass;
- this Candidate stays inactive globally and is active only for this exact workflow-migration scope after successful exact-head validation.
