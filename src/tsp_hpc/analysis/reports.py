"""Optional pandas/Matplotlib presentation over the reusable analysis API."""

from copy import deepcopy
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
import matplotlib

from tsp_hpc.analysis import read_shards, summary_table, paired_comparisons, reference_gaps
from tsp_hpc.reproducibility.identity import canonical_json
from tsp_hpc.experiments.runner import runtime_provenance


def _figure(height):
    figure = Figure(figsize=(12, height), layout="constrained")
    FigureCanvasAgg(figure)
    return figure


def _scale(values):
    maximum = float(np.max(np.abs(values)))
    return maximum if maximum > 1e100 or 0 < maximum < 1e-100 else 1.0


def _plots(analysis, summary, comparisons, gaps, output, limit):
    files, notes = [], []
    groups = summary.loc[summary["usable_count"] > 0] if not summary.empty else summary
    if groups.empty:
        return files, ["No usable completed observations to plot."]
    if len(groups) > limit:
        return files, [f"Static plots omitted: {len(groups)} groups exceeds limit {limit}; all tables are retained."]
    ids = groups["group_id"].tolist()
    labels = [f"{row['label']} | {row['implementation']} | {row['distance_mode']} | cities={row['n_cities']} | starts={row['budget_count']} | usable={row['usable_count']}/{row['attempt_count']}"
              for row in groups.to_dict("records")]
    height = max(3.5, len(ids) * 0.45 + 1.5)
    figure = _figure(height)
    for index, metric in enumerate(("search_time_s", "end_to_end_time_s"), start=1):
        axis = figure.add_subplot(1, 2, index)
        scale = _scale(groups[f"{metric}_max"].to_numpy(dtype=float))
        median = groups[f"{metric}_median"].to_numpy(dtype=float) / scale
        bounds = np.array([median - groups[f"{metric}_min"].to_numpy(dtype=float) / scale,
                           groups[f"{metric}_max"].to_numpy(dtype=float) / scale - median])
        axis.errorbar(median, np.arange(len(ids)), xerr=bounds, fmt="o", capsize=3)
        axis.set_yticks(np.arange(len(ids)), labels if index == 1 else groups["label"].tolist())
        title = "Search time" if metric == "search_time_s" else "End-to-end time"
        axis.set_xlabel(f"{title} (seconds)" if scale == 1 else f"{title} / {scale:g} seconds")
        axis.grid(axis="x", alpha=0.25)
    figure.suptitle("Fixed-work runtime: median and min–max; groups are not pooled")
    figure.savefig(output / "runtime.png", dpi=140)
    files.append("runtime.png")

    figure = _figure(height)
    axis = figure.add_subplot()
    usable = analysis.trials[analysis.trials["usable"]]
    scale = _scale(groups["best_cost_max"].to_numpy(dtype=float))
    values = [usable.loc[usable["group_id"] == group, "best_cost"].to_numpy(dtype=float) / scale for group in ids]
    axis.boxplot(values, orientation="horizontal", tick_labels=labels, showmeans=True, showfliers=False)
    unit = "Tour cost" if scale == 1 else f"Tour cost / {scale:g}"
    axis.set_xlabel(f"{unit} — compare quality only within the same instance/objective")
    axis.set_title("Quality conditional on usable trials (one observation is not a distribution estimate)")
    figure.savefig(output / "quality.png", dpi=140)
    files.append("quality.png")

    if comparisons is not None and not comparisons.empty:
        valid = comparisons[comparisons["reason"].isna()]
        if not valid.empty:
            figure = _figure(height)
            axis = figure.add_subplot()
            scale = _scale(np.append(valid["speedup"].to_numpy(dtype=float), 1.0))
            by_group = {group: values["speedup"].to_numpy(dtype=float) / scale
                        for group, values in valid.groupby("group_id", sort=False)}
            shown = [group for group in ids if group in by_group]
            for index, group in enumerate(shown):
                ratios = by_group[group]
                median = np.median(ratios)
                axis.errorbar([median], [index], xerr=[[median - ratios.min()], [ratios.max() - median]],
                              fmt="o", capsize=3, color="tab:blue")
            axis.axvline(1 / scale, linestyle="--", color="grey")
            pair_labels = [labels[ids.index(group)] + f" | pairs={len(by_group[group])}" for group in shown]
            axis.set_yticks(np.arange(len(shown)), pair_labels)
            unit = "Paired baseline / target duration" + ("" if scale == 1 else f" / {scale:g}")
            axis.set_xlabel(f"{unit} (dashed line = equal durations)")
            axis.set_title(f"Paired ratios: median and min–max of {comparisons['metric'].iloc[0]} — not parallel efficiency")
            figure.savefig(output / "speedup.png", dpi=140)
            files.append("speedup.png")
    if gaps is not None and not gaps.empty:
        figure = _figure(height)
        axis = figure.add_subplot()
        scale = _scale(gaps["absolute_gap"].to_numpy(dtype=float))
        for kind, marker in (("exact", "o"), ("best_known", "x")):
            subset = gaps[gaps["reference_kind"] == kind]
            for index, (group, values) in enumerate(subset.groupby("group_id", sort=False)):
                values = values["absolute_gap"].to_numpy(dtype=float) / scale
                median = np.median(values)
                axis.errorbar([median], [ids.index(group)],
                              xerr=[[median - values.min()], [values.max() - median]],
                              fmt=marker, capsize=3, color="tab:blue" if kind == "exact" else "tab:orange",
                              label=f"{kind} reference" if index == 0 else None)
        axis.set_yticks(np.arange(len(ids)), labels)
        unit = "Absolute cost gap" if scale == 1 else f"Absolute cost gap / {scale:g}"
        axis.set_xlabel(f"{unit} (percent gaps and sources are in gaps.csv)")
        axis.set_title("Reference gaps: median and min–max; exact ≠ best-known")
        axis.legend()
        figure.savefig(output / "gaps.png", dpi=140)
        files.append("gaps.png")
    return files, notes


def write_report(paths, *, output_dir, expected_manifest=None, references=None,
                 baseline_implementation=None, baseline_distance_mode=None,
                 metric="search_time_s", max_plot_groups=40) -> dict:
    """Create a new report directory, never modifying shards or replacing output.

    CSVs retain all observations and explicit exclusion flags/counts. Plots use
    only usable observations. Missing history/parallel data is stated, not faked.
    The data/aggregation APIs remain independent of this static renderer.
    """
    if metric not in ("search_time_s", "end_to_end_time_s"):
        raise ValueError("invalid comparison metric")
    if type(max_plot_groups) is not int or max_plot_groups < 1:
        raise ValueError("max_plot_groups must be a positive integer")
    if baseline_distance_mode is not None and baseline_implementation is None:
        raise ValueError("a baseline distance mode requires a baseline implementation")
    references = deepcopy(references)
    analysis = read_shards(paths, expected_manifest=expected_manifest)
    summary = summary_table(analysis)
    summary.insert(0, "label", [f"G{i+1}" for i in range(len(summary))])
    comparisons = None if baseline_implementation is None else paired_comparisons(
        analysis, baseline_implementation=baseline_implementation,
        baseline_distance_mode=baseline_distance_mode, metric=metric)
    gaps = None if references is None else reference_gaps(analysis, references)
    output = Path(output_dir)
    output.mkdir()  # Exclusive creation; a failed render leaves evidence, not an overwritten report.
    analysis.trials.to_csv(output / "trials.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)
    if comparisons is not None:
        comparisons.to_csv(output / "comparisons.csv", index=False)
    if gaps is not None:
        gaps.to_csv(output / "gaps.csv", index=False)
    # Isolate reports from user matplotlibrc styles (including external TeX).
    with matplotlib.rc_context({**matplotlib.rcParamsDefault, "text.parse_math": False}):
        figures, notes = _plots(analysis, summary, comparisons, gaps, output, max_plot_groups)
    notes.extend([
        "Quality-over-time and time-to-target are unavailable: runner-v1/v2 record no history.",
        "Parallel efficiency is unavailable: runner-v1/v2 execute serial NN only.",
        "Recorded tours/identities were checked; objective costs were not recomputed without input artifacts.",
        "Runtime context matching is observational, not hardware attestation or statistical significance.",
        "Warm-up/cache state and trial order can dominate end-to-end ratios; these are observed ratios, not controlled speedup conclusions.",
        "Failed, no-solution, duplicate-execution and incomplete-campaign observations are excluded from numeric aggregates, not from counts.",
        "Quality boxplots omit individual outlier markers; all observations remain in tables. Speed-ratio plots summarize paired ratios, not ratios of aggregate medians.",
    ])
    if references is None:
        notes.append("Reference gaps unavailable: no explicit compatible references supplied.")
    elif gaps.empty:
        notes.append("No usable observations matched supplied references.")
    if baseline_implementation is None:
        notes.append("Speed ratios unavailable: no explicit baseline supplied.")
    elif comparisons.empty:
        notes.append("No non-baseline observations available for comparison.")
    blocked = 0 if comparisons is None else int(comparisons["reason"].notna().sum())
    report = {
        "schema_version": 1, "sources": analysis.sources,
        "analysis_runtime": {**runtime_provenance(), "pandas": pd.__version__, "matplotlib": matplotlib.__version__},
        "configuration": {"expected_manifest": None if expected_manifest is None else expected_manifest.to_dict(),
                          "references": references, "max_plot_groups": max_plot_groups},
        "observed_trials": len(analysis.trials), "diagnostics": analysis.diagnostics,
        "comparison": {"baseline_implementation": baseline_implementation,
                       "baseline_distance_mode": baseline_distance_mode, "metric": metric,
                       "blocked_pairs": blocked},
        "figures": figures, "notes": notes,
        "has_issues": bool(analysis.diagnostics or blocked),
    }
    (output / "report.json").write_text(canonical_json(report) + "\n", encoding="utf-8")
    return report
