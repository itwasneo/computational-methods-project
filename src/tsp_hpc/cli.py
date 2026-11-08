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
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    # Keep help/version independent of numerical imports and JIT startup.
    from tsp_hpc.manifests import Manifest
    from tsp_hpc.runner import run_manifest
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
