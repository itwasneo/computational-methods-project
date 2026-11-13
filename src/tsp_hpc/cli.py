"""Command-line entry point for sequential manifest campaigns."""

import argparse
from collections.abc import Sequence

from tsp_hpc import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Show package information or execute a fixed-work manifest."""
    parser = argparse.ArgumentParser(
        prog="tsp-hpc",
        description=(
            "TSP heuristics and HPC experiment tools. "
            "Run fixed-work serial NN campaigns from manifests."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="execute a sequential manifest campaign")
    run.add_argument("manifest", help="manifest JSON path")
    run.add_argument("--instance", action="append", default=[], metavar="ID=PATH",
                     help="bind a manifest instance ID to a saved artifact; repeat for multiple inputs")
    run.add_argument("--output", required=True, help="new JSONL shard; parent directory must exist")
    run.add_argument("--max-matrix-bytes", type=int,
                     help="dense matrix ceiling per trial (not total process memory)")
    analyze = commands.add_parser("analyze", help="validate result shards and generate tables/plots")
    analyze.add_argument("shards", nargs="+", help="runner JSONL shards")
    analyze.add_argument("--output", required=True, help="new report directory")
    analyze.add_argument("--manifest", help="optional expected manifest for detecting absent executions")
    analyze.add_argument("--references", help="explicit objective-compatible reference-cost JSON")
    analyze.add_argument("--baseline", help="explicit baseline implementation for paired speed ratios")
    analyze.add_argument("--baseline-distance-mode", choices=("dense", "on_demand"))
    analyze.add_argument("--metric", choices=("search_time_s", "end_to_end_time_s"), default="search_time_s")
    analyze.add_argument("--max-plot-groups", type=int, default=40)
    serve = commands.add_parser("serve", help="start the optional read-only local workspace API")
    serve.add_argument("--workspace", required=True, help="fixed workspace directory for this session")
    serve.add_argument("--port", type=int, default=8000, help="127.0.0.1 port (default: 8000)")
    serve.add_argument("--session-secret-stdin", action="store_true",
                       help="read a private startup secret from stdin; disables terminal re-pairing controls")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    # Keep help/version and the optional server independent of numerical imports/JIT.
    if args.command == "serve":
        try:
            from tsp_hpc.interface.launcher import serve as serve_workspace
        except ModuleNotFoundError:
            parser.exit(2, "Interface dependencies unavailable; use uv sync --locked --extra interface or install tsp-hpc[interface].\n")
        from tsp_hpc.interface.errors import InterfaceError
        try:
            return serve_workspace(args.workspace, port=args.port, session_secret_stdin=args.session_secret_stdin)
        except (InterfaceError, ValueError) as error:
            parser.exit(2, f"tsp-hpc: {error}\n")
    from tsp_hpc.experiments.manifests import Manifest
    if args.command == "analyze":
        try:
            from tsp_hpc.analysis import load_references
            from tsp_hpc.analysis.reports import write_report
        except ModuleNotFoundError as error:
            parser.exit(2, f"Analysis dependencies unavailable ({error}); in the checkout use uv run --locked --group analysis tsp-hpc analyze ...\n")
        try:
            report = write_report(
                args.shards, output_dir=args.output,
                expected_manifest=None if args.manifest is None else Manifest.from_file(args.manifest),
                references=None if args.references is None else load_references(args.references),
                baseline_implementation=args.baseline, baseline_distance_mode=args.baseline_distance_mode,
                metric=args.metric, max_plot_groups=args.max_plot_groups,
            )
        except (OSError, ValueError, TypeError) as error:
            parser.exit(2, f"tsp-hpc: {error}\n")
        print(f"{report['observed_trials']} observations; {len(report['diagnostics'])} diagnostics; "
              f"{report['comparison']['blocked_pairs']} blocked comparisons; report: {args.output}")
        return 1 if report["has_issues"] else 0
    from tsp_hpc.experiments.runner import run_manifest
    bindings = {}
    for binding in args.instance:
        identifier, separator, path = binding.partition("=")
        if not separator or not identifier or not path or identifier in bindings:
            parser.error("--instance requires unique ID=PATH bindings")
        bindings[identifier] = path
    options = {} if args.max_matrix_bytes is None else {"max_matrix_bytes": args.max_matrix_bytes}
    try:
        summary = run_manifest(Manifest.from_file(args.manifest), instance_paths=bindings,
                               output_path=args.output, **options)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, f"tsp-hpc: {error}\n")
    counts = summary["counts"]
    print(f"{counts['completed']} completed, {counts['failed']} failed; shard: {args.output}")
    return 1 if counts["failed"] else 0
