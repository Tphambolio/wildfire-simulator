"""Held-line hold of an observed starting fire (backlog A1, docs/active-segments.md).

Uniform C-2, west wind, a 600 m square start with only its east (head) edge active. Known
answers:

- ``hold_hours=None`` is the earlier behaviour bit for bit (held cells burned out for the run);
- ``hold_hours=0`` is the explicit "all active" option: identical to no ``active_edges``;
- ``H > 0``: during the hold nothing burns in the barrier ring around the held sides (the
  flanking fire from the active edge cannot creep along the outside of a held line), and after
  the release the held west side backs into the west fuel as a whole-perimeter fire does;
- a hold longer than the run never releases: the west side stays held throughout;
- with no active cell at all, the fire waits for the release, then spreads.
"""

import numpy as np
from scipy import ndimage

from .test_active_edges import C0, C1, COND, EAST_EDGE, LAT0, LNG0, N, R0, R1, SQUARE, _grid

from firesim.spread.cellular import run_cellular_simulation


def _run(active=EAST_EDGE, minutes=60.0, **kw):
    frames = run_cellular_simulation(
        {"ignition_lat": LAT0, "ignition_lng": LNG0, "duration_hours": minutes / 60.0}, _grid(),
        COND, initial_perimeter=SQUARE, snapshot_interval_minutes=minutes, compute_perimeter=False,
        active_edges=active, **kw)
    return frames[-1].arrival


def _start():
    m = np.zeros((N, N), bool)
    m[R0:R1, C0:C1] = True
    return m


def test_unset_hold_is_the_earlier_behaviour():
    np.testing.assert_array_equal(_run(), _run(hold_hours=None, hold_band_m=None))


def test_hold_zero_is_all_active():
    np.testing.assert_array_equal(_run(active=None), _run(hold_hours=0.0))


def test_barrier_ring_holds_then_releases():
    start = _start()
    west = np.zeros((N, N), bool)
    west[R0:R1, :C0] = True
    held = _run(hold_hours=0.5)  # 30 min hold, 60 min run
    never = _run(hold_hours=2.0)  # longer than the run
    # During the hold (arrival <= 30 min) no cell of the 1-cell ring around the held cells
    # burns except next to the active east column
    held_cells = start.copy()
    held_cells[:, C1 - 1] = False
    held_cells[:, C1 - 2] = False
    ring = ndimage.binary_dilation(held_cells, structure=np.ones((3, 3), bool)) & ~start
    ring[:, C1 - 3:] = False
    assert not np.any(held[ring] <= 30.0)
    assert not np.any(never[ring] < np.inf)
    # After the release the west side backs into the west fuel; with no release it never does
    assert np.isfinite(held[west]).sum() > 0
    assert np.all(held[west][np.isfinite(held[west])] > 30.0)
    assert np.isfinite(never[west]).sum() == 0
    # The head run east is not affected by the hold
    def east_reach(a):
        return np.nonzero(np.isfinite(a[R0:R1]).any(axis=0))[0].max()
    assert abs(east_reach(held) - east_reach(_run())) <= 1


def test_nothing_active_waits_for_release():
    far = {"type": "Point", "coordinates": [LNG0 + 0.05, LAT0 + 0.05]}
    a = _run(active=far, hold_hours=0.25, minutes=45.0)
    start = _start()
    grown = np.isfinite(a) & ~start
    assert grown.sum() > 0
    assert np.all(a[grown] > 15.0)
    b = _run(active=far, minutes=45.0)
    assert (np.isfinite(b) & ~start).sum() == 0
