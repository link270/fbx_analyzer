This repository includes tests and utilities to help validate changes.

Agent guidelines

- After every code change, run the full test suite locally.
- If any test fails, inspect the failure output, attempt a fix, and run the full suite again.
- Make 2–3 iterative attempts to fix failing tests automatically before reporting the failure back for human review.
- Do not add unrelated features while fixing tests; keep changes focused and minimal.
- Prefer adding or adjusting targeted tests only when they increase clarity or lock in intended behavior.

Test execution

- The test suite uses `pytest`.
- Some tests require the Autodesk FBX SDK Python bindings (`fbx`, `FbxCommon`). If the SDK is not available on the environment, those tests will be skipped automatically.
- Tests reference example FBX files under `tests/files`.
- By default, tests delete any FBX files they save during round‑trip checks. To keep outputs for inspection, set the environment variable `FBX_ANALYZER_KEEP_TEST_OUTPUTS=1`.

