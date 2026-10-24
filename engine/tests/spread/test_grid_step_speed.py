"""The grid model's faster front step (backlog E10, 2026-10-24) gives the same numbers as the
previous full-window step, and progress counts only the minutes in which fire can spread.

``_reference_advance`` below is the front step as it was before E10 (every cell of the window,
per-quadrant diagonal differences), kept here as the oracle: the new step works on the
window's fuel cells only, which must change nothing, bit for bit.
"""

import math

import numpy as np
import pytest

from firesim.data.synthetic_grid import generate_synthetic_fuel_grid
from firesim.spread import cellular
from firesim.spread.cellular import (BAND_CELLS, _advance, _CellParams, _cell_keys, _signed_distance,
                                     _spread_minutes, _window, _window_within, _WindowStatic,
                                     run_cellular_simulation)
from firesim.spread.diurnal import burning_period_schedule
from firesim.spread.huygens import SpreadConditions


def _reference_advance(phi, win, p, near_nonfuel, dx, dy, step, t, arrival, cross_ros,
                       acceleration=True, t_ign=0.0):
    sub = phi[win]
    pad = np.pad(sub, 2, mode="edge")
    C = pad[2:-2, 2:-2]
    W1, W2, E1, E2 = pad[2:-2, 1:-3], pad[2:-2, :-4], pad[2:-2, 3:-1], pad[2:-2, 4:]
    S1, S2, N1, N2 = pad[3:-1, 2:-2], pad[4:, 2:-2], pad[1:-3, 2:-2], pad[:-4, 2:-2]
    fpad = np.pad(p.fuel[win], 1, mode="edge")
    W1 = np.where(fpad[1:-1, :-2], W1, C)
    E1 = np.where(fpad[1:-1, 2:], E1, C)
    S1 = np.where(fpad[2:, 1:-1], S1, C)
    N1 = np.where(fpad[:-2, 1:-1], N1, C)
    dxm, dxp = (C - W1) / dx, (E1 - C) / dx
    dym, dyp = (C - S1) / dy, (N1 - C) / dy

    def mm(u, v):
        return np.where(np.abs(u) < np.abs(v), u, v)

    eno = ~near_nonfuel
    dxm = dxm + eno * 0.5 * mm(C - 2 * W1 + W2, E1 - 2 * C + W1) / dx
    dxp = dxp - eno * 0.5 * mm(E1 - 2 * C + W1, E2 - 2 * E1 + C) / dx
    dym = dym + eno * 0.5 * mm(C - 2 * S1 + S2, N1 - 2 * C + S1) / dy
    dyp = dyp - eno * 0.5 * mm(N1 - 2 * C + S1, N2 - 2 * N1 + C) / dy
    a, b, c = p.a[win], p.b[win], p.c[win]
    hx, hy, fuel = p.hx[win], p.hy[win], p.fuel[win]
    if acceleration:
        alpha = p.alpha[win]
        ta = t - t_ign
        g = 1.0 - (np.exp(-alpha * ta) - np.exp(-alpha * (ta + step))) / (alpha * step)
        lb_t = (p.lb[win] - 1.0) * (1.0 - np.exp(-alpha * (ta + 0.5 * step))) + 1.0
        a, c = a * g, c * g
        b = a / lb_t
    gx, gy = 0.5 * (dxm + dxp), 0.5 * (dym + dyp)
    g = np.hypot(gx, gy) + 1e-12
    nh = (gx * hx + gy * hy) / g
    nk = (-gx * hy + gy * hx) / g
    root = np.sqrt(a * a * nh * nh + b * b * nk * nk) + 1e-12
    uh = c + a * a * nh / root
    uk = b * b * nk / root
    ux = uh * hx - uk * hy
    uy = uh * hy + uk * hx
    rows, cols = C.shape

    def at(dr, dc):
        return pad[2 + dr:2 + dr + rows, 2 + dc:2 + dc + cols]

    def fuel_at(dr, dc):
        return fpad[1 + dr:1 + dr + rows, 1 + dc:1 + dc + cols]

    def open_corner(dr, dc):
        return fuel_at(dr, dc) & (fuel_at(dr, 0) | fuel_at(0, dc))

    sr = np.where(uy > 0, 1, -1)
    sc = np.where(ux > 0, -1, 1)
    diag = np.zeros_like(C)
    for dr in (-1, 1):
        for dc in (-1, 1):
            sel = (sr == dr) & (sc == dc)
            if not sel.any():
                continue
            p1 = np.where(open_corner(dr, dc), at(dr, dc), C)
            f1 = np.where(open_corner(-dr, -dc), at(-dr, -dc), C)
            d = (C - p1) + eno * 0.5 * mm(C - 2 * p1 + at(2 * dr, 2 * dc), f1 - 2 * C + p1)
            diag = np.where(sel, d, diag)
    along_x = np.where(ux > 0, dxm, -dxp) * dx
    along_y = np.where(uy > 0, dym, -dyp) * dy
    qx, qy = np.abs(ux) / dx, np.abs(uy) / dy
    rate = np.where(qx >= qy, (qx - qy) * along_x + qy * diag, (qy - qx) * along_y + qx * diag)
    new = np.where(fuel, C - step * rate, np.maximum(C, 0.5 * min(dx, dy)))
    crossed = fuel & (C >= 0) & (new < 0)
    if crossed.any():
        frac = C[crossed] / np.maximum(C[crossed] - new[crossed], 1e-12)
        arr_win = arrival[win]
        ros_win = cross_ros[win]
        arr_win[crossed] = t + frac * step
        ros_win[crossed] = (c * nh + root)[crossed]
    phi[win] = new


def _cond(ws=25.0, wd=250.0):
    return SpreadConditions(wind_speed=ws, wind_direction=wd, ffmc=91.0, dmc=45.0, dc=300.0,
                            grass_cure=95.0)


@pytest.fixture(scope="module")
def case():
    """A synthetic 50 m grid (fuel with non-fuel patches) and a burned blob near its middle."""
    grid = generate_synthetic_fuel_grid(53.5, -113.5, radius_km=2.0, cell_size_m=50.0, seed=3)
    rows, cols = grid.rows, grid.cols
    cell_lat = (grid.lat_max - grid.lat_min) / rows
    cell_lng = (grid.lng_max - grid.lng_min) / cols
    dy = cell_lat * 111320.0
    dx = cell_lng * 111320.0 * math.cos(math.radians((grid.lat_max + grid.lat_min) / 2))

    def center(r, c):
        return grid.lat_max - (r + 0.5) * cell_lat, grid.lng_min + (c + 0.5) * cell_lng

    params = _CellParams.evaluate(_cell_keys(grid, None, None, center), _cond())
    rr, cc = np.mgrid[:rows, :cols]
    blob = ((rr - rows / 2) ** 2 / 64 + (cc - cols / 2) ** 2 / 25 < 1) & params.fuel
    return params, _signed_distance(blob, dx, dy), blob, dx, dy


@pytest.mark.parametrize("acceleration", [True, False])
def test_fuel_cell_step_equals_the_full_window_step(case, acceleration):
    params, phi0, blob, dx, dy = case
    from scipy import ndimage

    near_nonfuel = ndimage.binary_dilation(~params.fuel, iterations=2)
    win = _window(blob, BAND_CELLS)
    assert (~params.fuel[win]).any() and params.fuel[win].any()  # both kinds in the window
    for step in (0.05, 0.2, 0.7):
        out = []
        for fn, kw in ((_reference_advance, {}), (_advance, {}),
                       (_advance, {"static": _WindowStatic(params, win, near_nonfuel[win])})):
            phi = phi0.copy()
            arrival = np.where(blob, 0.0, np.inf)
            cross = np.zeros_like(phi)
            for i in range(40):  # several steps so the front crosses cells
                fn(phi, win, params, near_nonfuel[win], dx, dy, step, 60.0 + i * step, arrival,
                   cross, acceleration, 1.0, **kw)
            out.append((phi, arrival, cross))
        ref = out[0]
        assert np.isfinite(ref[1]).sum() > blob.sum()  # the fire moved
        for got in out[1:]:
            for a, b in zip(ref, got):
                assert np.array_equal(a, b)


def test_window_within_equals_the_full_grid_window():
    rng = np.random.default_rng(1)
    arrival = np.full((60, 80), np.inf)
    for _ in range(20):
        r, c = rng.integers(10, 50), rng.integers(10, 70)
        arrival[r, c] = 1.0
        win = _window(arrival < np.inf, BAND_CELLS)
        assert _window_within(arrival, win, BAND_CELLS) == win
    # Near the grid edge the margin is clipped the same way
    arrival[0, 0] = arrival[59, 79] = 2.0
    big = (slice(0, 60), slice(0, 80))
    assert _window_within(arrival, big, BAND_CELLS) == _window(arrival < np.inf, BAND_CELLS)


def test_spread_minutes_count_only_the_burning_period():
    sched = burning_period_schedule([(0.0, _cond())], 0.5, (10, 20), 1440.0)  # start 00:30
    done = _spread_minutes(sched, 1440.0)
    assert done(1440.0) == pytest.approx(600.0)
    assert done(9.5 * 60) == pytest.approx(0.0)  # 10:00 local
    assert done(14.5 * 60) == pytest.approx(300.0)  # 15:00
    assert done(23 * 60) == pytest.approx(600.0)
    # No burning period: clock minutes
    assert _spread_minutes([(0.0, _cond())], 120.0)(60.0) == pytest.approx(60.0)


def test_progress_runs_over_the_burning_minutes_without_jumps():
    grid = generate_synthetic_fuel_grid(53.5, -113.5, radius_km=2.0, cell_size_m=50.0, seed=7)
    sched = burning_period_schedule([(0.0, _cond(20.0, 270.0))], 6.0, (10, 20), 1440.0)
    seen = []
    run_cellular_simulation({"ignition_lat": 53.5, "ignition_lng": -113.5, "duration_hours": 24},
                            grid, sched[0][1], weather_schedule=sched, progress=seen.append)
    assert seen and seen == sorted(seen) and seen[-1] <= 1.0
    # Before E10 the first report after the night was 4/24 of the day at once; now each report
    # is at most a little over the 1 % reporting step
    assert max(b - a for a, b in zip(seen, seen[1:])) < 0.05
    assert cellular.CFL == 0.2  # the Courant number is unchanged
