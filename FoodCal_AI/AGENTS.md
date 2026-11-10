# FoodCal AI agent instructions

Scope: this directory only. Read README.md, HANDOFF.md and INTEGRATION_REPORT.md before changes. Run commands from FoodCal_AI, not the team repository root. Preserve other teammates' files and existing changes. Inspect branch and git status before editing; the handoff branch is feature/foodcal-ai-handoff.

## Evidence boundary

Recipe candidates may explain hidden calories, but are not visual proof. Do not infer visible ingredients from dish labels, turn recipe quantities into photo grams, promote mocks or AI drafts to ground truth, invent approvals, or fill unavailable calories. Keep uncertainty, provenance, test_only/review_only and independent human review explicit. Portion and nutrition estimation are unfinished.

## Data and sharing

Do not commit data/, runs/, work/, outputs/, models/, weights/, images, private manifests, annotation revisions, model replies, TFDA snapshots, credentials, environments or agent sessions. Do not copy the original project's .git or local audit directories. Only reviewed code, synthetic fixtures, settings and sanitized documentation may enter this subtree. Extend the exact .gitignore allowlist only after review; never force-add ignored evidence. Do not grant a new open-source license or assume external data/model rights.

## Validation

Run `python -B -m unittest discover -s tests -v` and `python -B -m src.pipeline.diagnostic_demo`. For annotation changes run the annotation modules listed in HANDOFF.md. Use synthetic temporary images/workspaces for tests. Report pass/fail/error/skip counts and skip reasons separately. Missing private evidence is not a passing test; do not fabricate replacement evidence or silently relax validators. Optional Streamlit and Tk tests require their respective runtimes.

## Working rules

Preserve append-only revisions and hashes; do not rewrite existing annotations or automatically accept AI candidates. AI proposal import does not execute a model. No training, automatic downloads, live model calls, Commit, Push, Merge or Force Push without an explicit new user instruction. Do not modify the original project or delete local backups. Keep handoff documentation aligned with actual code and observed tests, not historical success claims.
