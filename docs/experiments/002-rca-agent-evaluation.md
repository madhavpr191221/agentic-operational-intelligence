# RCA Investigator Evaluation Design

## Evaluation boundary

Evaluate the detector and investigator as separate components. Detector thresholds are calibrated from control scenarios and scored on held-out seeds by `scripts/evaluate_detectors.py`. RCA evaluation uses known synthetic evidence labels only in the evaluator; the agent and its tools never receive the planted scenario cause.

## Case set

Use reproducible `payment_issue`, `healthy`, `unrelated_issue`, and `resolved_issue` runs. Build each selected signal from that run's actual detector score and matching 30-minute evidence window; do not substitute fabricated alert scores. The payment case checks supported cause reasoning, while healthy, unrelated, and resolved cases check abstention or appropriately qualified findings. Keep calibration seeds `0–99` separate from evaluation seeds `100–149`; add new seed ranges for later regression runs.

## Measures

- **Cause support:** for positive cases, the top hypothesis cites evidence across matching payment tickets, payment-service errors, and an open payment issue. Report the rate across seeds.
- **Control overclaim rate:** for healthy, unrelated, or resolved evidence, count hypotheses that claim an unsupported payment cause.
- **Citation validity:** valid cited evidence IDs divided by all cited IDs; target 100% after application-side filtering.
- **Evidence coverage:** whether the investigation searched relevant evidence sources and found records within the selected signal's run.
- **Analysis-tool coverage:** whether the investigator used category comparison, reason/channel contribution ranking, timeline ordering, and before/after comparison.
- **Message grounding:** whether a supported explanation cites a customer message whose text matches the reported failure mode.
- **Recommendation grounding:** every actionable runbook suggestion must cite evidence returned in that investigation; evidence-free cases should ask for more data.
- **Efficiency:** record model turns, read-only tool calls, duration, and safety-ceiling hits; do not reward extra calls when they add no relevant evidence.
- **Abstention:** verify that weak or contradictory evidence can produce no ranked cause and clear unresolved questions.

## Review protocol

First inspect saved tool traces for missed evidence, repeated searches, and unsupported claims. Then review structured metrics across held-out seeds. Keep a small fixed regression set for every prompt, tool, detector-context, or report-schema change. Treat model-judge scores as supplementary; use known evidence IDs and hidden synthetic labels for primary grading.

## Demo acceptance

From the browser, start the seeded payment scenario, select the payments alert, and run an investigation. The UI must show the detector context, customer message text, ordered tool starts/results, a final hypothesis whose citations resolve to displayed evidence, and advisory next steps with a verification check. Repeat with mock mode to demonstrate the same UI offline. Healthy and resolved controls should not be described as proven payment causes. Each email/chat/comment represented here is one ticket record; text is evidence attached to that count, not an extra count.

## Multi-Category Regressions

Select only categories with alert episodes and score each category finding independently. Include a mixed case where one category is supported and another must abstain. A shared-cause claim is supported only when separate evidence backs every named category. Keep episode grouping and representative-window selection deterministic for ties.

Add these measures:

- **Category isolation:** unselected categories must not appear in investigator inputs, findings, or hypotheses.
- **Multi-category precision:** score supported and unsupported findings per selected category; score shared-cause claims separately against evidence from each named category.

For the browser demo, compare all four independently scaled charts, select an episode and representative alert window, then investigate. Verify detector context, ordered tool activity, and per-category citations. When multiple episodes are available, check that each selected category receives its own finding and that shared causes require evidence across categories.
