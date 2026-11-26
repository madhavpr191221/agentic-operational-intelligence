"""Render the offline joint-count benchmark JSON as report and SVG figures."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/multi-category-counts"
DATA = RESULTS / "stage-1-offline-statistical-evaluation.json"


def svg_tradeoff(data: dict) -> str:
    methods = data["methods"]
    healthy = ("healthy_independent", "healthy_common_volume", "overdispersed_independent")
    signal = ("shared_sync", "shared_delay_1", "shared_delay_2", "shared_delay_3", "shared_delay_4",
              "independent_simultaneous", "independent_delayed", "single_category", "beyond_horizon")
    width, height = 900, 480
    x0, y0, plotw, ploth = 90, 50, 730, 330
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#fff"/>',
             '<style>text{font-family:Arial,sans-serif;fill:#20242a}.small{font-size:12px}.title{font-size:18px;font-weight:bold}.grid{stroke:#d7dce2;stroke-width:1}.axis{stroke:#303840;stroke-width:1.3}</style>',
             '<text class="title" x="450" y="25" text-anchor="middle">Held-out detection vs healthy-stream episode rate</text>']
    for j in range(5):
        y = y0 + ploth - j*ploth/4
        parts += [f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x0+plotw}" y2="{y:.1f}"/>',
                  f'<text class="small" x="{x0-10}" y="{y+4:.1f}" text-anchor="end">{j/4:.2f}</text>']
    maxx = max(1.0, max(s["mean_episodes_per_stream"] for m in methods.values() for scenario in healthy for s in m["scenarios"][scenario]["threshold_sweep"]))
    for i in range(5):
        x = x0 + i*plotw/4
        parts += [f'<line class="grid" x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" y2="{y0+ploth}"/>',
                  f'<text class="small" x="{x:.1f}" y="{y0+ploth+20}" text-anchor="middle">{maxx*i/4:.1f}</text>']
    parts += [f'<line class="axis" x1="{x0}" y1="{y0+ploth}" x2="{x0+plotw}" y2="{y0+ploth}"/>',
              f'<line class="axis" x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0+ploth}"/>',
              f'<text class="small" x="{x0+plotw/2}" y="{height-20}" text-anchor="middle">Mean joint episodes per seven-day healthy stream</text>',
              f'<text class="small" transform="translate(20 {y0+ploth/2}) rotate(-90)" text-anchor="middle">Mean detection rate across planted pattern scenarios</text>']
    colors = {"positive_residual_scan": "#1769aa", "shared_gamma_poisson": "#c45118"}
    for name, method in methods.items():
        for qi, q in enumerate(data["quantiles"]):
            xval = sum(method["scenarios"][s]["threshold_sweep"][qi]["mean_episodes_per_stream"] for s in healthy)/len(healthy)
            yval = sum(method["scenarios"][s]["threshold_sweep"][qi]["detection_rate"] for s in signal)/len(signal)
            x = x0 + min(1, xval/maxx)*plotw
            y = y0 + ploth - yval*ploth
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{colors[name]}"/><text class="small" x="{x+8:.1f}" y="{y-7:.1f}">q={q:g}</text>')
    ref=data["existing_per_category_reference"]
    rxv=sum(ref[s]["mean_joint_episodes_per_stream"] for s in healthy)/len(healthy)
    ryv=sum(ref[s]["detection_rate"] for s in signal)/len(signal)
    rx=x0+min(1,rxv/maxx)*plotw; ry=y0+ploth-ryv*ploth
    parts.append(f'<polygon points="{rx:.1f},{ry-8:.1f} {rx+8:.1f},{ry:.1f} {rx:.1f},{ry+8:.1f} {rx-8:.1f},{ry:.1f}" fill="#303840"/><text class="small" x="{rx+10:.1f}" y="{ry-7:.1f}">reference</text>')
    parts += [f'<polygon points="455,420 461,426 455,432 449,426" fill="#303840"/><text class="small" x="466" y="430">Current reference</text>',
              f'<circle cx="600" cy="425" r="6" fill="{colors["positive_residual_scan"]}"/><text class="small" x="612" y="429">Positive residual scan</text>',
              f'<circle cx="785" cy="425" r="6" fill="{colors["shared_gamma_poisson"]}"/><text class="small" x="797" y="429">Gamma-Poisson</text>', '</svg>']
    return "\n".join(parts)


def svg_lags(data: dict) -> str:
    width, height = 900, 420
    x0, y0, plotw, ploth = 100, 55, 700, 280
    labels = ["Sync", "1 window", "2 windows", "3 windows", "4 windows"]
    scenarios = ["shared_sync", "shared_delay_1", "shared_delay_2", "shared_delay_3", "shared_delay_4"]
    colors = {"positive_residual_scan": "#1769aa", "shared_gamma_poisson": "#c45118"}
    p = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="420" viewBox="0 0 900 420"><rect width="100%" height="100%" fill="#fff"/>',
         '<style>text{font-family:Arial,sans-serif;fill:#20242a}.small{font-size:12px}.title{font-size:18px;font-weight:bold}.grid{stroke:#d7dce2}.axis{stroke:#303840;stroke-width:1.3}</style>',
         '<text class="title" x="450" y="25" text-anchor="middle">Exact lag recovery on held-out shared-pattern streams</text>']
    for j in range(5):
        val=j/4; y=y0+ploth-val*ploth
        p += [f'<line class="grid" x1="{x0}" y1="{y}" x2="{x0+plotw}" y2="{y}"/>',f'<text class="small" x="{x0-10}" y="{y+4}" text-anchor="end">{val:.2f}</text>']
    for name, method in data["methods"].items():
        points=[]
        for i,s in enumerate(scenarios):
            sweep=method["scenarios"][s]["threshold_sweep"]
            # The center calibration quantile is shown consistently; all five are in JSON.
            val=sweep[2]["exact_lag_rate"] or 0
            x=x0+i*plotw/4; y=y0+ploth-val*ploth
            points.append((x,y))
            p.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{colors[name]}"/>')
        p.append(f'<polyline fill="none" stroke="{colors[name]}" stroke-width="2" points="{" ".join(f"{x},{y}" for x,y in points)}"/>')
    for i,label in enumerate(labels):
        x=x0+i*plotw/4;p.append(f'<text class="small" x="{x}" y="{y0+ploth+20}" text-anchor="middle">{label}</text>')
    p += [f'<line class="axis" x1="{x0}" y1="{y0+ploth}" x2="{x0+plotw}" y2="{y0+ploth}"/>',
          f'<line class="axis" x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0+ploth}"/>',
          '<text class="small" transform="translate(22 195) rotate(-90)" text-anchor="middle">Exact category and lag recovery rate</text>',
          '<circle cx="610" cy="390" r="5" fill="#1769aa"/><text class="small" x="620" y="394">Positive residual scan</text>',
          '<circle cx="760" cy="390" r="5" fill="#c45118"/><text class="small" x="770" y="394">Gamma-Poisson</text></svg>']
    return "\n".join(p)


def report(data: dict) -> str:
    qidx = 2
    lines = ["# Stage 1 — Offline statistical evaluation of multi-category counts", "",
        f"**Status:** Completed offline evaluation · **Replicates:** {data['seeds_per_partition']} per null scenario for fitting and calibration; {data['seeds_per_partition']} per scenario held out · **Scored horizon:** seven days (336 half-hour windows)", "",
        "> This evaluates candidate count-only joint signals. It does not integrate them into the live detector, create incidents, or identify causes.", "",
        "## Executive summary", "",
        "This report compares the existing per-category detector as a reference with two offline candidate scores. Thresholds are calibrated from independent null streams, then evaluated on held-out streams. No candidate threshold is selected for production. Read the threshold trade-off and held-out results together; a detection rate without its healthy-stream alert rate is incomplete.", "",
        "## Experiment contract", "",
        f"- Categories and baseline means per 30-minute window: `{', '.join(data['categories'])}` and `{data['baseline_means']}`.",
        f"- Clean fit history: {data['warmup_windows']} windows (24 h); then {data['scored_windows']} scored windows (7 d). The planted event starts at an independent seeded position after at least 48 scored windows.",
        f"- Joint lag horizon: current completed window plus the previous {data['lag_horizon']} windows. Each lag step is 30 minutes.",
        f"- Disjoint seeds: train `{data['partition_ranges']['train'][0]}–{data['partition_ranges']['train'][1]-1}`, calibration `{data['partition_ranges']['calibration'][0]}–{data['partition_ranges']['calibration'][1]-1}`, held-out `{data['partition_ranges']['heldout'][0]}–{data['partition_ranges']['heldout'][1]-1}`.",
        "- Fit data contain only healthy independent, healthy common-volume, and over-dispersed independent streams. Thresholds use only calibration null streams. The held-out partition is never used for fitting or threshold selection.",
        "- Counts are the only candidate input. Scenario names, affected categories, onset, and lag are separate evaluator metadata and are never passed to score functions.",
        "", "### Fitted null parameters", "",
        "| Category | Fitted mean / window | Fitted variance |", "|---|---:|---:|"]
    for i,category in enumerate(data["categories"]):
        lines.append(f"| {category} | {data['fit']['means'][i]:.4f} | {data['fit']['variances'][i]:.4f} |")
    lines += ["", f"Estimated shared Gamma shape: $k={data['fit']['common_shape']:.4f}$. Same-window residual correlations:", "",
        "| | Payments | Orders | Account | Delivery |", "|---|---:|---:|---:|---:|"]
    for i,category in enumerate(data["categories"]):
        lines.append("| " + category + " | " + " | ".join(f"{data['fit']['correlations'][i][j]:.3f}" for j in range(4)) + " |")
    lines += ["", "## Candidate methods", "",
        "For category $c$ and completed window $t$, training estimates its expected count $\\hat{\\mu}_c$, variance $\\hat{v}_c$, and same-window residual correlation $\\hat{\\rho}_{cd}$. Positive standardized residuals are",
        "", "$$R^+_{c,t}=\\max\\left(0,\\frac{Y_{c,t}-\\hat{\\mu}_c}{\\sqrt{\\hat{v}_c}}\\right).$$", "",
        "### A. Positive residual scan", "",
        "For each unordered category pair $(c,d)$, independently choose the largest positive residual in the five-window lookback. The pair score discounts expected co-movement learned from training:",
        "", "$$S^{\\mathrm{res}}_t=\\max_{c<d}\\frac{R^+_{c,t-\\ell_c}+R^+_{d,t-\\ell_d}}{\\sqrt{2+2\\hat{\\rho}_{cd}}},\\qquad \\ell_c,\\ell_d\\in\\{0,1,2,3,4\\}.$$", "",
        "The maximizing pair and its two lags are retained for offline localization metrics. The denominator is regularized below by $\\sqrt{0.5}$ to avoid numerical instability. This is a positive co-exceedance scan, not a causal or general-purpose multivariate model.", "",
        "### B. Shared Gamma–Poisson predictive score", "",
        "For a selected pair of category-window observations, the null treats expected rates as fixed independent Poisson means. The alternative uses a shared multiplicative volume factor $G\\sim\\operatorname{Gamma}(k,k)$ with mean one. For pair counts $y_i$, expected means $\\mu_i$, total $N=\\sum_i y_i$, and $M=\\sum_i\\mu_i$, the log Bayes factor is:",
        "", "$$S^{\\mathrm{GP}}=\\log\\Gamma(k+N)-\\log\\Gamma(k)+k\\log k-(k+N)\\log(k+M)+M.$$", "",
        "The score is truncated at zero and maximized across category pairs and the 0–4 window lag choices. $k$ is estimated from training stream totals and bounded to $[0.5,100]$. A larger $k$ approaches a fixed shared rate; smaller $k$ permits more common-volume variation. This model tests a shared multiplicative count fluctuation; it does not imply a shared operational cause.", "",
        "### Reference: current per-category detectors", "",
        "The existing negative-binomial category detector and its checked-in calibrated thresholds are run unchanged. The reference joint flag is at least two distinct category alerts within the same five-window lookback. This post-processing is solely an evaluation comparator; it does not change or combine live alerts.", "",
        "## Scenario matrix", "",
        "| Scenario | Planted count pattern | What it checks |", "|---|---|---|",
        "| Healthy independent | No planted change; independent Poisson | Nominal null behavior |",
        "| Healthy common volume | No fault; shared mean-one Gamma volume factor | Normal correlated volume should not be mistaken automatically for a fault |",
        "| Shared synchronous | Payments and Account elevated together | Same-window detection and localization |",
        "| Shared delayed 1–4 | Payments pulse, Account pulse after 1–4 windows | Lag search and lag localization |",
        "| Independent simultaneous / delayed | Similar marginals, distinct planted pulses | Detect unusual joint patterns without claiming shared cause |",
        "| Single category | Payments alone elevated | Joint signal should not be the only route for univariate alerts |",
        "| Beyond horizon | Second category begins 7 windows later; first pulse lasts 3 windows | Expected miss outside the declared search horizon, including pulse persistence |",
        "| Over-dispersed independent | Independent Gamma–Poisson category rates | Robustness to marginal over-dispersion |", "",
        "Each event pulse multiplies its category rate by 3.6 for three windows. These are controlled synthetic fixtures, not estimates of production incident prevalence.", "",
        "## Held-out results", "",
        "The table gives the center of the predeclared threshold sweep (97.5th percentile of calibration-stream maximum scores). It is a comparison slice, not a recommended operating threshold. For planted patterns, the rate is the proportion detected at or after the earliest planted effect; for healthy scenarios, it is the proportion of full streams with at least one false joint signal. Rates have 95% Wilson intervals over independent held-out seeds. Episode counts are means with seed-level bootstrap 95% intervals.", "",
        "| Candidate | Scenario | Detection / false-alarm stream rate (95% CI) | Episodes / stream (95% CI) | Mean delay (windows, planted only) | Contributor recall | Exact lag |", "|---|---|---:|---:|---:|---:|---:|"]
    for method, body in data["methods"].items():
        for scenario, value in body["scenarios"].items():
            row=value["threshold_sweep"][qidx]
            ci=row["detection_rate_95ci"]
            dr=f"{row['detection_rate']:.3f} ({ci[0]:.3f}–{ci[1]:.3f})"
            fmt=lambda x: "—" if x is None else f"{x:.3f}"
            epi_ci=row["mean_episodes_95ci"]
            epi=f"{row['mean_episodes_per_stream']:.3f} ({epi_ci[0]:.3f}–{epi_ci[1]:.3f})" if epi_ci else f"{row['mean_episodes_per_stream']:.3f}"
            lines.append(f"| {method} | {scenario} | {dr} | {epi} | {fmt(row['mean_detection_delay_windows'])} | {fmt(row['mean_contributor_recall'])} | {fmt(row['exact_lag_rate'])} |")
    lines += ["", "### Calibrated threshold sweep", "", "Threshold values are the calibration-partition quantiles of the per-stream maximum score over the full seven-day stream.", "",
        "| Candidate | Calibration max quantile | Threshold |", "|---|---:|---:|"]
    for method,body in data["methods"].items():
        for q,threshold in zip(data["quantiles"],body["thresholds"]):
            lines.append(f"| {method} | {q:.3f} | {threshold:.6f} |")
    lines += ["", "### Existing detector reference", "", "| Scenario | Detection / false-alarm stream rate (95% CI) | Joint episodes / stream (95% CI) | Mean delay (planted only) |", "|---|---:|---:|---:|"]
    for scenario,value in data["existing_per_category_reference"].items():
        ci=value["detection_rate_95ci"]
        delay="—" if value["mean_detection_delay_windows"] is None else f"{value['mean_detection_delay_windows']:.3f}"
        epi_ci=value["mean_joint_episodes_95ci"]
        lines.append(f"| {scenario} | {value['detection_rate']:.3f} ({ci[0]:.3f}–{ci[1]:.3f}) | {value['mean_joint_episodes_per_stream']:.3f} ({epi_ci[0]:.3f}–{epi_ci[1]:.3f}) | {delay} |")
    def metric(method: str, scenario: str, field: str) -> float:
        return data["methods"][method]["scenarios"][scenario]["threshold_sweep"][qidx][field]
    residual_sync=metric("positive_residual_scan","shared_sync","detection_rate")
    residual_common=metric("positive_residual_scan","healthy_common_volume","detection_rate")
    gamma_sync=metric("shared_gamma_poisson","shared_sync","detection_rate")
    gamma_common=metric("shared_gamma_poisson","healthy_common_volume","detection_rate")
    ref_sync=data["existing_per_category_reference"]["shared_sync"]["detection_rate"]
    ref_common=data["existing_per_category_reference"]["healthy_common_volume"]["detection_rate"]
    delay4=metric("positive_residual_scan","shared_delay_4","detection_rate")
    lines += ["", "### What these results say", "",
        f"- At this calibrated threshold slice, the residual scan detects {residual_sync:.1%} of synchronous planted patterns and produces at least one false signal in {residual_common:.1%} of healthy common-volume streams. The shared Gamma–Poisson score is {gamma_sync:.1%} and {gamma_common:.1%}, respectively. These are modest hit rates; the Gamma–Poisson candidate barely separates this planted pattern from normal common-volume movement.",
        f"- The existing per-category reference detects {ref_sync:.1%} of synchronous patterns with {ref_common:.1%} of healthy common-volume streams falsely signaling under the same five-window category-alert aggregation. It has lower sensitivity here, but also a lower common-volume false-alarm rate than either candidate.",
        f"- The residual scan detects {delay4:.1%} of the shared four-window delayed patterns at this threshold. Mean detection delays and end-to-end lag localization are shown in the table; the 30-minute sampling interval limits timing precision.",
        "- Single-category and beyond-horizon patterns should not yield joint signals; the measured rates are reported in the table. Existing per-category detection remains the path for a one-category issue.",
        "- The independent-simultaneous fixture deliberately has the same observed count distribution as the shared-synchronous fixture. Their equal scores demonstrate the boundary: counts can show an unusual joint pattern, but cannot tell us whether the underlying events share a cause.",
        "- These results do not justify promoting either score to the live detector. A later stage should first review and, if needed, revise the controlled scenario assumptions or compare additional calibrated candidates; no production false-alert budget was supplied.", ""]
    lines += ["", "## Threshold sensitivity and localization", "", "![Held-out trade-off across calibrated threshold quantiles](stage-1-threshold-tradeoff.svg)", "", "![Held-out exact lag recovery](stage-1-lag-localization.svg)", "",
        "The trade-off chart averages detection across nine planted scenarios and mean episode counts across three healthy scenarios. It is descriptive; the scenario mix is artificial and no deployment operating point follows from this average. Exact lag recovery is evaluated only for the two-category shared patterns; every threshold quantile is retained in the JSON companion.", "",
        "## Uncertainty and interpretation", "",
        "Held-out planted-detection and healthy-stream false-alarm rates use Wilson 95% intervals. Mean episode counts and mean detection delay use percentile bootstrap 95% intervals resampling independent seeds (1,000 draws; fixed bootstrap seed 943). Contributor recall and exact-lag rates are end-to-end: a missed signal contributes zero. These intervals describe Monte Carlo uncertainty under these generators, not uncertainty about real-world performance. The five thresholds are fixed from calibration maxima and held constant across all held-out scenarios.", "",
        "Statistical association is not causal attribution. Independent simultaneous or delayed events may trigger a joint statistical pattern if their vector is rare. The later RCA stage must use separate, evidence-grounded reasoning to assess shared cause, separate causes, or uncertainty.", "",
        "## Reproduction", "",
        "From the repository root, run:", "", "```powershell", "uv run python -m agentic_ops.evaluation.joint_count_eval --seeds-per-partition 500", "uv run python scripts/render_joint_count_results.py", "uv run pytest tests/test_joint_count_eval.py -q", "```", "",
        "The first command writes the machine-readable benchmark results beside this report. `--quick` runs a 12-seed smoke check and must not be cited as performance evidence. The generated SVGs are self-contained and require no charting dependency.", "",
        "## Limits and Stage 1 disposition", "",
        "- The fixture uses four fixed categories and stationary baseline means; seasonality, weekday effects, missing windows, changing ticket mix, and category definition drift are not modeled.",
        "- Same-window training correlation is used by the residual candidate; delayed dependence is searched over 0–4 lags but is not explicitly learned as a lag-specific null covariance.",
        "- The Gamma–Poisson alternative is a shared-rate fluctuation model, not a full learned joint distribution.",
        "- No synthetic labels reach scoring, but generator design still determines what patterns are represented.",
        "- No production threshold, alert budget, application wiring, agent tool, queue behavior, or UI is delivered in Stage 1.",
        "- Stage 1 is complete as an offline measurement artifact. Candidate promotion requires review of the held-out trade-offs and a later design decision." , ""]
    return "\n".join(lines)


def main() -> None:
    data=json.loads(DATA.read_text(encoding="utf-8"))
    RESULTS.mkdir(parents=True,exist_ok=True)
    (RESULTS/"stage-1-threshold-tradeoff.svg").write_text(svg_tradeoff(data),encoding="utf-8")
    (RESULTS/"stage-1-lag-localization.svg").write_text(svg_lags(data),encoding="utf-8")
    (RESULTS/"stage-1-offline-statistical-evaluation.md").write_text(report(data),encoding="utf-8")
    print("Rendered Stage 1 Markdown report and SVG figures")

if __name__=="__main__": main()
