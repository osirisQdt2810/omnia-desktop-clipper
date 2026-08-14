## Context
<!-- Why is this change needed? What problem does it solve? -->
<!-- Link related issues or incidents -->
- Related issue:
- Background / motivation:
- Constraints / assumptions:

---

## Content / Changes
<!-- What exactly changed in this PR -->
- 
- 
- 

<!-- Optional: call out non-obvious changes -->
- Refactors:
- New features:
- Removed / deprecated behavior:

---

## Test Plan
<!-- How was this change validated -->

### Test Details
<!-- Commands, configs, or steps used to test -->
- 

### Test Output / Feature Demonstration
<!-- Paste test output, logs, screenshots, benchmarks, or example requests/responses -->
- 

---

## Desktop clipper checklist
<!-- Delete a line only when it genuinely cannot apply. "N/A — why" is a valid answer. -->

- **Platforms**: this ships on Windows + macOS + Ubuntu. Exercised on:
  <!-- CI covers all three; say so, or name what you ran by hand and where -->
- **Packaging**: does this change the PyInstaller spec, an entry point, or a bundled data file?
  A frozen build resolves paths differently from a source run — say which you tested.
- **Dependencies**: a new dependency must build on all three platforms. Name it and why it is
  needed; prefer stdlib.
- **Message contract with the add-on**: does this change what is sent to Omnia? Both sides must
  survive a version skew — an older Omnia receiving this payload must not break.
- **User-visible change / migration**: what will an existing user notice after the update?
