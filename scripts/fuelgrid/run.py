"""One-command entry point for the open-data fuel-grid pipeline.

    python -m scripts.fuelgrid.run all          # fetch -> labels -> build -> validate
    python -m scripts.fuelgrid.run fetch        # download open inputs onto the region grid
    python -m scripts.fuelgrid.run labels       # Edmonton LiDAR crown labels (City data, local only)
    python -m scripts.fuelgrid.run build        # conifer model + decision key -> grids
    python -m scripts.fuelgrid.run validate     # Edmonton vs LiDAR grid, CFS baseline, St. Albert
    python -m scripts.fuelgrid.run firesim      # load each grid in the FireSim engine and spread

Version 2 (full FBP key + disturbance; Edmonton, St. Albert and northern Alberta test areas):

    python -m scripts.fuelgrid.run all2         # fetch2 -> build2 -> validate2 (needs the v1 build)
    PYTHONPATH=engine/src python -m scripts.fuelgrid.run firesim2

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
    ap.add_argument("stage", choices=["all", "fetch", "labels", "build", "validate", "firesim",
                                          "all2", "fetch2", "build2", "validate2", "firesim2"])
    ap.add_argument("--force", action="store_true", help="refetch / rebuild even if outputs exist")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    from . import build, labels, sources, validate
    stages = {"all": ["fetch", "labels", "build", "validate"],
              "all2": ["fetch2", "build2", "validate2"]}.get(args.stage, [args.stage])
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
        elif st == "fetch2":
            from . import sources2
            sources2.fetch_all_v2(force=args.force)
        elif st == "build2":
            from . import build2
            build2.build_all_v2()
        elif st == "validate2":
            from . import validate2
            validate2.validate_all_v2()
        elif st == "firesim2":
            from . import validate2
            validate2.firesim_check_v2()
    return 0


if __name__ == "__main__":
    sys.exit(main())
