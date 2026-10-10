"""One-command entry point for the open-data fuel-grid pipeline.

    python -m scripts.fuelgrid.run all          # fetch -> labels -> build -> validate
    python -m scripts.fuelgrid.run fetch        # download open inputs onto the region grid
    python -m scripts.fuelgrid.run labels       # Edmonton LiDAR crown labels (City data, local only)
    python -m scripts.fuelgrid.run build        # conifer model + decision key -> grids
    python -m scripts.fuelgrid.run validate     # Edmonton vs LiDAR grid, CFS baseline, St. Albert
    python -m scripts.fuelgrid.run firesim      # load each grid in the FireSim engine and spread

Data go to $FUELGRID_DATA (default ~/dev/wildfire/fuelgrid-data); nothing is written to the repo.
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings

warnings.filterwarnings("ignore", message="X does not have valid feature names")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["all", "fetch", "labels", "build", "validate", "firesim"])
    ap.add_argument("--force", action="store_true", help="refetch / rebuild even if outputs exist")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    from . import build, labels, sources, validate
    stages = ["fetch", "labels", "build", "validate"] if args.stage == "all" else [args.stage]
    for st in stages:
        if st == "fetch":
            sources.fetch_all(force=args.force)
        elif st == "labels":
            labels.build_labels(force=args.force)
        elif st == "build":
            build.build_all()
        elif st == "validate":
            validate.validate_all()
        elif st == "firesim":
            from . import firesim_check
            firesim_check.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
