# Tasks 001: Live Ticket Spike Detection and RCA

## Stage A - Foundation

- [x] Create clean-room charter and roadmap.
- [x] Define the first scenario and interface contract.
- [x] Set up package, settings, and SQLite schema.

## Stage B - Synthetic Data and Detection

- [x] Implement seeded Poisson and negative-binomial event generation.
- [x] Add positive, healthy, resolved, and unrelated-issue scenarios.
- [x] Implement LLR, standardized residual, upward CUSUM, and Gamma-Poisson BF.
- [x] Add calibration and held-out evaluation report.

## Stage C - Evidence and RCA

- [x] Implement read-only query tools with evidence IDs.
- [x] Add single-agent OpenAI Agents SDK investigator with adaptive tool use and deterministic mock mode.
- [x] Verify truth isolation and report grounding.

## Stage D - Local Operator Experience

- [x] Add run/event/investigation API and SSE stream.
- [x] Build UI for scenario controls, timeline, alert, and RCA evidence.
- [x] Document local startup and optional OpenAI configuration.

## Stage E - Later Metrics

- [ ] Add CSAT and escalation metric definitions (deferred; ticket counts are the current vertical slice).
- [ ] Select and test metric-specific models before adding further metrics.

## Stage F - Category Signals and Visible RCA Activity

- [x] Generate category counts and score each category independently.
- [x] Calibrate category-specific thresholds and extend held-out detector evaluation.
- [x] Pass structured detector scores and window metadata into the investigator.
- [x] Persist tool lifecycle progress and display it in the incident room.
- [x] Define synthetic RCA evaluation cases and add a live evaluation command.
- [ ] Run the backend suite, frontend build, detector evaluation, and a small live/mock browser walkthrough before release.

## Stage G - Category Comparison and Multi-Category RCA

- [x] Replace the combined category curve with four small-multiple charts using independent scales.
- [x] Group consecutive alerts into category episodes, select a representative alert window, and allow one selected episode per category.
- [x] Extend the investigation API with a bounded multi-alert request while preserving single-alert compatibility.
- [x] Return category-level findings and keep cross-category cause hypotheses separate and evidence-gated.
- [x] Update the product spec and evaluation design for category-specific selection and review.
- [ ] Run backend checks and frontend build; review episode selection and category reports in the browser.

## Stage H - Message-Level Investigation and Practical RCA Analysis

- [x] Generate one reproducible synthetic message body and observable reason per ticket without changing detector count inputs.
- [x] Add read-only category comparison, reason/channel contribution, timeline, and before/after analysis tools.
- [x] Add evidence-grounded, non-executing runbook recommendations with verification steps.
- [x] Show customer message text, cited evidence records, and suggested actions in the incident room.
- [x] Extend the RCA evaluation report for analytical-tool coverage and recommendation grounding; add deterministic regression coverage without launching a multi-seed paid evaluation.
- [x] Run the backend suite, frontend build, and held-out detector evaluation.
- [ ] Inspect the live investigation UI in a browser; browser automation was unavailable during this implementation pass.
