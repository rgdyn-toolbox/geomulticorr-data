"""Build ``MS_<Site>_<Sensor>`` from the site's image bank, as a GMC user would.

Usage: ``python scripts/build_project.py <site> <sensor>`` (run ``fetch_bank.py`` first).
"""

from __future__ import annotations

import sys

from run_pipeline import run_all
from study_site import load_sensor


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit(__doc__)
    sensor = load_sensor(argv[1], argv[2])
    if not sensor.bank_dir.is_dir():
        raise SystemExit(f"no bank at {sensor.bank_dir}: run fetch_bank.py first")
    session = run_all(sensor)
    print(session.path_root)


if __name__ == "__main__":
    main(sys.argv)
