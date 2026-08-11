# Project Plan

Shared working plan for AI assistants and human collaborators in this folder.

## How to use

- Update this file when priorities change.
- Mark items as `TODO`, `IN PROGRESS`, `BLOCKED`, or `DONE`.
- Keep proposed tasks concrete enough that another AI can pick them up quickly.
- Add short notes when a task has assumptions or dependencies.

## Suggested Task Template

```md
- [STATUS] Task title
  Owner:
  Why:
  Next step:
  Notes:
```

---

## Active Priorities

- [IN PROGRESS] Validate end-to-end `.mpr` raw-file workflow inside the main pipeline
  Owner: Any AI working on code improvements
  Why: Raw EC-Lab file support is now added, but the full GUI-driven batch path has not yet been run through a complete sample pair after the loader refactor.
  Next step: Run one representative `PEIS .mpr + CA .mpr` pair through `Start_here.py` and confirm Excel export, FFT recovery, and fitting all still behave as expected.
  Notes: Loader-level tests passed on `Input data/260417-8`.

- [TODO] Decide whether `yadg` should be an explicit project dependency
  Owner: Any AI working on environment or setup
  Why: `.mpr` support now depends on `yadg`, and the current environment showed a `packaging` version conflict warning during installation.
  Next step: Document the dependency in setup notes or isolate it in a dedicated environment if needed.
  Notes: `eclabfiles` was tested and failed on current project `.mpr` files.

- [TODO] Maintain a clean shared handoff process across multiple AI assistants
  Owner: Any AI working in this folder
  Why: Prevent duplicated effort and make it easy to understand recent work.
  Next step: Append to `UPDATE_LOG.md` after each meaningful change and refresh this plan when priorities shift.
  Notes: Use simple factual summaries and include generated artifact names.

- [TODO] Refine the rapid EIS presentation for actual lab or professor-facing use
  Owner: Any AI working on slides
  Why: The user is actively preparing how to explain the project and its value.
  Next step: Review the newest presentation file, tighten slide wording, and align tone with the target audience.
  Notes: Current slide artifacts include Korean versions and an FFT explanation slide.

- [TODO] Improve the explanation of FFT and the CP-to-EIS conversion logic
  Owner: Any AI working on communication or education materials
  Why: This is currently one of the main conceptual pain points in the presentation.
  Next step: Consider a simpler visual showing `time-domain response -> FFT -> frequency-domain response -> impedance`.
  Notes: Prioritize intuition over formal math unless the user asks for more depth.

- [TODO] Review whether the timing-comparison claims versus conventional EIS should be formalized
  Owner: Any AI working on technical validation
  Why: Time-saving claims are persuasive, but they should be stated carefully and consistently.
  Next step: Document the assumptions behind the `~5 h`, `~43 h`, `x30`, and `x250` comparisons.
  Notes: Best outcome is a clear method for how those estimates were derived.

- [TODO] Consider making the rapid EIS workflow easier to run non-interactively
  Owner: Any AI working on code improvements
  Why: Batch reproducibility and automation will help future users and other AIs.
  Next step: Evaluate whether `Start_here.py` should support command-line arguments or a config file instead of only dialogs and console prompts.
  Notes: Keep current interactive behavior unless the user requests a workflow change.

- [TODO] Revisit `Recommended PEIS lowest freq` and `Recommended PEIS conservative CP time` before any GUI/runtime integration
  Owner: Any AI working on adaptive measurement logic
  Why: The current recommendation outputs are useful, but they are not yet considered fully mature enough to drive the measurement GUI directly.
  Next step: Reload this project after the recommendation logic is more complete and validate the outputs against more real datasets before treating them as trusted runtime controls.
  Notes: Internal prototypes may use these values with conservative safety margins, but the Microprobe GUI should not depend on them yet.

- [TODO] Develop and validate a trend-aware adaptive measurement policy prototype inside the analysis project only
  Owner: Any AI working on adaptive logic
  Why: We want to test whether previous optimized points, electrode history, gas/temperature trends, and conservative margins can guide the next measurement without touching the GUI yet.
  Next step: Use the prototype policy to simulate next-point recommendations and refine the lookup / trend rules with real measurement history.
  Notes: Prefer same-electrode history over blindly copying the immediately previous point from a different electrode.

## Parking Lot

- [TODO] Add a short README focused on project purpose, data flow, and output files
  Owner: Unassigned
  Why: New collaborators can orient themselves faster.
  Next step: Summarize the main modules and typical outputs in one page.
  Notes: Could later include example input and result-folder structure.

- [TODO] Check whether generated PPT files should be versioned in a dedicated subfolder
  Owner: Unassigned
  Why: Presentation artifacts may accumulate and become hard to track.
  Next step: Decide whether to keep them in the root folder or move them to a `presentations/` directory.
  Notes: Coordinate before moving existing user-facing files.
