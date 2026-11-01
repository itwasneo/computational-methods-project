"""Support ``python -m tsp_hpc`` alongside the installed console command."""

from tsp_hpc.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
