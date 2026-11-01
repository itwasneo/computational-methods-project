"""Command-line entry point; experiment commands are added in later tasks."""

import argparse
from collections.abc import Sequence

from tsp_hpc import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Show package information without claiming a solver is implemented."""
    parser = argparse.ArgumentParser(
        prog="tsp-hpc",
        description=(
            "TSP heuristics and HPC experiment tools. "
            "Package scaffold only: solver and benchmark commands are not yet implemented."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
