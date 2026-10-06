"""Building exposure: Cohen (2004) radiant model, flux-time criterion and grid-run exposure.

References:
    - Cohen, J.D. (2004). Can. J. For. Res. 34: 1616-1626. SIAM radiant flux (eq 1), worked
      values 79 / 45 / 27 kW/m2 at 10 / 20 / 30 m from a 20 m x 50 m crown-fire flame at 1200 K
      (pp.1622-1623), flux-time ignition criterion (eqs 2-4) and the 31 kW/m2 / 60 s example
      (p.1618).
"""

import math

import numpy as np
import pytest
import shapely

from firesim.exposure import (
    EMISSIVE_POWER_COHEN,
    Emitters,
    building_exposure,
    centred_flame_view_factor,
    flux_time_product,
    FTP_IGNITION,
    rectangle_view_factor,
    time_to_ignition_s,
)
from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.huygens import FuelGrid, SpreadConditions
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, WeatherInput

LAT0, LNG0 = 53.5, -113.5
COND = SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=92.0, dmc=40.0, dc=300.0)


class TestCohenRadiantModel:
    @pytest.mark.parametrize("d, published", [(10.0, 79.0), (20.0, 45.0), (30.0, 27.0)])
    def test_reproduces_cohen_worked_values(self, d, published):
        q = centred_flame_view_factor(50.0, 20.0, d) * EMISSIVE_POWER_COHEN
        assert abs(q - published) / published < 0.035

    def test_blackbody_emissive_power_1200k(self):
        assert EMISSIVE_POWER_COHEN == pytest.approx(117.6, abs=0.05)

    def test_superposition_matches_centred_form(self):
        assert rectangle_view_factor(-25, 25, -10, 10, 10.0) == pytest.approx(
            centred_flame_view_factor(50.0, 20.0, 10.0))

    def test_split_panels_sum_to_whole(self):
        whole = rectangle_view_factor(-25, 25, -10, 10, 10.0)
        parts = sum(rectangle_view_factor(x, x + 5, -10, 10, 10.0) for x in range(-25, 25, 5))
        assert parts == pytest.approx(whole)

    def test_time_to_ignition_cohen_example(self):
        # Cohen p.1618: 31 kW/m2 for about 60 s reaches the criterion
        assert time_to_ignition_s(31.0) == pytest.approx(60.0, rel=0.05)
        assert time_to_ignition_s(13.0) == math.inf

    def test_ftp_constant_flux(self):
        t = time_to_ignition_s(40.0)
        assert flux_time_product([40.0], [t]) == pytest.approx(FTP_IGNITION)
        assert flux_time_product([12.0], [1e6]) == 0.0


def _straight_front(width=50.0, height=20.0, n=50, start=0.0, end=1.0):
    """A straight flame front along x at y = 0 facing +y, split into n panels."""
    w = width / n
    x = -width / 2 + w * (np.arange(n) + 0.5)
    return Emitters(
        x=x, y=np.zeros(n), start_min=np.full(n, start), end_min=np.full(n, end),
        flame_m=np.full(n, height), normal_x=np.zeros(n), normal_y=np.ones(n), cell_size=w,
    )


class TestBuildingExposure:
    def test_panel_front_reproduces_cohen_at_10m(self):
        rec = building_exposure([(0.0, 0.0, shapely.Point(0.0, 10.0))], _straight_front(),
                                duration_min=10.0, use_footprints=False)[0]
        assert rec.peak_flux == pytest.approx(80.6, abs=0.2)
        assert rec.min_distance_m == pytest.approx(9.51, abs=0.01)  # nearest panel centre less half a panel
        assert rec.band == "flame_contact"
        assert rec.minutes_over_12_5 == pytest.approx(1.0)
        # 1 minute at 80.6 kW/m2 is well past the criterion (t_ig ~ 5 s)
        assert rec.ftp_index > 1.0
        assert rec.first_ftp_reached_min == pytest.approx(
            time_to_ignition_s(rec.peak_flux) / 60.0, rel=1e-6)
        assert rec.ftp_index_high > rec.ftp_index

    def test_footprint_wall_is_the_target(self):
        # a 10 m square house whose south wall is 20 m from the front
        house = shapely.box(-5.0, 20.0, 5.0, 30.0)
        rec = building_exposure([(0.0, 0.0, house)], _straight_front(),
                                duration_min=10.0, use_footprints=True)[0]
        point = building_exposure([(0.0, 0.0, shapely.Point(0.0, 20.0))], _straight_front(),
                                  duration_min=10.0, use_footprints=False)[0]
        assert rec.peak_flux == pytest.approx(point.peak_flux, rel=0.05)
        assert 40.0 < rec.peak_flux < 50.0  # Cohen: 45 kW/m2 at 20 m
        assert rec.band == "radiant"

    def test_flux_stops_at_end_of_run(self):
        rec = building_exposure([(0.0, 0.0, shapely.Point(0.0, 10.0))],
                                _straight_front(start=0.0, end=5.0),
                                duration_min=2.0, use_footprints=False)[0]
        assert rec.minutes_over_12_5 == pytest.approx(2.0)

    def test_distant_building_unexposed(self):
        rec = building_exposure([(0.0, 0.0, shapely.Point(0.0, 800.0))], _straight_front(),
                                duration_min=10.0, use_footprints=False)[0]
        assert rec.band == "none" and rec.peak_flux == 0.0


def _grid_with_house(n=80, cell_m=25.0, house_cells=((40, 58), (40, 59), (41, 58), (41, 59))):
    dlat = n * cell_m / 111320.0
    dlng = n * cell_m / (111320.0 * math.cos(math.radians(LAT0)))
    fuel = [[FuelType.C2] * n for _ in range(n)]
    for r, c in house_cells:
        fuel[r][c] = None
    return FuelGrid(fuel, LAT0 - dlat / 2, LAT0 + dlat / 2, LNG0 - dlng / 2, LNG0 + dlng / 2, n, n)


class TestGridExposure:
    def test_emitters_face_the_spread_direction(self):
        g = _grid_with_house(house_cells=())
        frames = run_cellular_simulation(
            dict(ignition_lat=LAT0, ignition_lng=LNG0, duration_hours=1.0), g, COND)
        em = frames[-1].emitters
        assert em is not None and len(em.x) == frames[-1].total_burned
        x0, y0 = 40 * 25.0, -40 * 25.0
        head = (em.x > x0 + 200.0) & (np.abs(em.y - y0) < 60.0)  # downwind, on the fire axis
        assert head.any() and np.median(em.normal_x[head]) > 0.9
        assert np.all(em.end_min > em.start_min)

    def test_simulator_reports_exposure_for_a_house_in_the_path(self):
        g = _grid_with_house()
        # house footprint occupies rows 40-41, cols 58-59 (about 450 m east of the ignition)
        cell_lat = (g.lat_max - g.lat_min) / g.rows
        cell_lng = (g.lng_max - g.lng_min) / g.cols
        house = shapely.box(g.lng_min + 58 * cell_lng, g.lat_max - 42 * cell_lat,
                            g.lng_min + 60 * cell_lng, g.lat_max - 40 * cell_lat)
        far = shapely.box(g.lng_min - 0.05, g.lat_min, g.lng_min - 0.049, g.lat_min + 0.001)
        config = SimulationConfig(LAT0, LNG0, WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0), 1.5,
                                  snapshot_interval_minutes=30.0, ffmc=92.0, dmc=40.0, dc=300.0)
        frames = list(Simulator(config, fuel_grid=g, building_footprints=[house, far]).run())
        last = frames[-1]
        assert last.building_exposure is not None
        assert frames[0].building_exposure["within_30m"] == 0
        assert last.building_exposure["within_30m"] == 1
        assert last.building_exposure["flux_over_12_5"] == 1
        # counts never decrease over time
        for k in ("within_100m", "flux_over_12_5", "ftp_reached"):
            vals = [f.building_exposure[k] for f in frames]
            assert vals == sorted(vals)
        assert all(f.building_exposure_detail is None for f in frames[:-1])
        detail = last.building_exposure_detail
        assert len(detail) == 1  # the far building is beyond 500 m
        assert detail[0]["band"] == "flame_contact" and detail[0]["peak_flux_kw_m2"] > 12.5

    def test_no_buildings_no_exposure(self):
        config = SimulationConfig(LAT0, LNG0, WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0), 0.5,
                                  ffmc=92.0, dmc=40.0, dc=300.0)
        frames = list(Simulator(config, fuel_grid=_grid_with_house()).run())
        assert all(f.building_exposure is None for f in frames)


def test_flux_capped_at_emissive_power_when_surrounded():
    # 50 m x 20 m flames on all four sides, 5 m away: the summed face-on view factors exceed 1
    side = np.linspace(-24.5, 24.5, 50)
    near = np.full(50, 5.0)
    x = np.concatenate([side, side, near, -near])
    y = np.concatenate([-near, near, side, side])
    nx = np.concatenate([np.zeros(100), -np.ones(50), np.ones(50)])
    ny = np.concatenate([np.ones(50), -np.ones(50), np.zeros(100)])
    em = Emitters(x=x, y=y, start_min=np.zeros(200), end_min=np.ones(200),
                  flame_m=np.full(200, 20.0), normal_x=nx, normal_y=ny, cell_size=1.0)
    rec = building_exposure([(0.0, 0.0, shapely.Point(0.0, 0.0))], em, duration_min=5.0,
                            use_footprints=False)[0]
    assert rec.peak_flux == pytest.approx(EMISSIVE_POWER_COHEN)
