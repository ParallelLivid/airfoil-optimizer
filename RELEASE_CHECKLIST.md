# Release preparation

The source release targets Windows with Python 3.13 and an independently installed XFOIL 6.99. It contains no external solver binary or source archive.

## Completed implementation

- [x] Custom geometry normalization and explicit coefficient/moment references.
- [x] Angle-resolution validation and unavailable derivatives for unresolved data.
- [x] Bounded solver logs, timeouts, and cancellation cleanup.
- [x] Per-row polar validation with discarded-row and coverage reporting.
- [x] Atomic settings replacement and UTF-8 BOM support.
- [x] Independent artifact-delivery reporting and figure cleanup on errors.
- [x] Numeric coordinate labels and bounded numerical inputs/output names.
- [x] Clean EOF/Ctrl+C handling at main and nested prompts.
- [x] Regression and real-solver tests for the publication findings.
- [x] MIT wrapper license, external-component notices, example configuration.
- [x] Explicit source manifest, Git exclusions, reproducible source ZIP builder.
- [x] Windows/Python 3.13 GitHub Actions regression workflow.

## Verification

Verified on 2026-09-10:

- 33 tests passed, including real XFOIL normalization, log-limit, cancellation, and concurrent-job checks.
- The source ZIP installed into a fresh Python 3.13 environment and passed the same regression and integration suite.
- The extended 27-case matrix returned complete/partial/failed results as appropriate; three unsupported numerical inputs were rejected before launching the solver.
- 24 repeated jobs used distinct directories and produced identical lift maxima; fault-injection checks preserved settings and correctly reported missing plots without leaking figures.
- The 800-point sweep completed all 800 points in 10.55 seconds with a 60-second budget.
- The 62-file release manifest includes the curated five-case example gallery and excludes solver binaries, environments, user settings, and local audit evidence. The Git file set is staged for review before the first commit.
- Final publication check: the 62-file package, including the example gallery, installed in a fresh environment and passed all 33 regression and real-solver tests. Archive CRCs and file contents matched the manifest; staged and working files matched; local Markdown links resolved; credential and personal-path pattern scans found no matches.
- The workflow's action versions exist upstream: [checkout v7](https://github.com/actions/checkout/releases/tag/v7.0.0) and [setup-python v7](https://github.com/actions/setup-python/releases/tag/v7.0.0). The hosted workflow itself remains untested until the first push.

Run `tools/verify_release.py --solver <local-xfoil-path>` to build and test the exact source ZIP in a fresh environment. Run the two opt-in stress scripts for broad solver coverage and the 800-point sweep. Neither process publishes anything.

Before pushing, inspect the staged diff and verify that the staged names match `release_files.txt`. Personal settings, historical reports, audit evidence, virtual environments, and solver installations are intentionally kept local. Do not upload the working folder wholesale through a browser file picker; use the reviewed Git file set or the generated source ZIP.

The GitHub Actions workflow is configured but can only be exercised on GitHub after a push. Tests verify application behavior and repeatability; they do not establish aerodynamic accuracy across the full accepted input range.
