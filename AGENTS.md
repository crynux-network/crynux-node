## Coding Requirements

Before adding new code, first check whether existing logic can be reused. Prefer extracting reusable code into a dedicated function, class, or file, and place it in the most appropriate location. Remove duplicated code and avoid adding redundant implementations of the same functionality.

Comments must describe final behavior only. Do not add comments that explain change history, such as what was added, removed, or why code was deleted. Keep comments concise and use them only for complex or non-obvious logic.

Do not add defensive fallback logic unless its purpose is explicit, requirement-backed, and tied to a concrete failure mode.

For WebUI-specific coding requirements, use `src/webui/AGENTS.md` as the source of truth.

## Debug Requirements

Follow a root-cause-first bugfix protocol: analyze the code path, form a clear hypothesis, validate it, and then apply a precise and minimal fix tied directly to the confirmed cause. Avoid speculative or broad defensive changes.

## E2E Test

For E2E test execution instructions, use `tests/e2e/AGENTS.md` as the source of truth.
