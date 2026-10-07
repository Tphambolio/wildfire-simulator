"""Validation metrics on synthetic shapes with known answers."""

import math

import numpy as np
import pytest
import shapely

from firesim.validation.metrics import (
    bearing_error_deg,
    hausdorff_m,
    head_spread,
    overlap_scores,
    polygon_hausdorff_m,
    polygon_overlap_scores,
    within_cruz_alexander,
)


def box_mask(shape, r0, r1, c0, c1):
    m = np.zeros(shape, dtype=bool)
    m[r0:r1, c0:c1] = True
    return m


class TestOverlapScores:
    def test_identical_masks_score_one(self):
        a = box_mask((20, 20), 5, 15, 5, 15)
        s = overlap_scores(a, a)
        assert s.precision == s.recall == s.f1 == s.iou == 1.0
        assert s.area_diff_norm == 0.0

    def test_half_overlapping_squares(self):
        # Two 10x10 squares offset by 5 columns: TP 50, FP 50, FN 50
        pred = box_mask((20, 30), 5, 15, 5, 15)
        obs = box_mask((20, 30), 5, 15, 10, 20)
        s = overlap_scores(pred, obs, cell_area_m2=100.0)
        assert s.tp == 5000.0 and s.fp == 5000.0 and s.fn == 5000.0
        assert s.precision == pytest.approx(0.5)
        assert s.recall == pytest.approx(0.5)
        assert s.f1 == pytest.approx(0.5)
        assert s.iou == pytest.approx(1 / 3)
        assert s.area_diff_norm == pytest.approx(0.0)

    def test_overprediction_area_difference(self):
        # Prediction 4x the observed area and containing it: P = 0.25, R = 1
        obs = box_mask((40, 40), 10, 20, 10, 20)
        pred = box_mask((40, 40), 10, 30, 10, 30)
        s = overlap_scores(pred, obs)
        assert s.precision == pytest.approx(0.25)
        assert s.recall == pytest.approx(1.0)
        assert s.f1 == pytest.approx(2 * 0.25 / 1.25)
        assert s.iou == pytest.approx(0.25)
        assert s.area_diff_norm == pytest.approx((400 - 100) / 500)

    def test_f1_is_dice_and_relates_to_iou(self):
        rng = np.random.default_rng(1)
        a, b = rng.random((50, 50)) > 0.6, rng.random((50, 50)) > 0.4
        s = overlap_scores(a, b)
        assert s.f1 == pytest.approx(2 * s.iou / (1 + s.iou))

    def test_empty_prediction_gives_nan_precision_and_zero_f1(self):
        obs = box_mask((10, 10), 2, 5, 2, 5)
        s = overlap_scores(np.zeros_like(obs), obs)
        assert math.isnan(s.precision)
        assert s.recall == 0.0 and s.f1 == 0.0 and s.iou == 0.0
        assert s.area_diff_norm == -1.0

    def test_shape_mismatch_rejected(self):
        with pytest.raises(ValueError):
            overlap_scores(np.zeros((3, 3), bool), np.zeros((3, 4), bool))

    def test_polygon_scores_match_raster_scores(self):
        a = shapely.box(0, 0, 1000, 1000)
        b = shapely.box(500, 0, 1500, 1000)
        s = polygon_overlap_scores(a, b)
        assert s.tp == pytest.approx(500_000)
        assert s.f1 == pytest.approx(0.5)
        assert s.iou == pytest.approx(1 / 3)


class TestHausdorff:
    def test_identical_is_zero(self):
        a = box_mask((10, 10), 2, 6, 2, 6)
        assert hausdorff_m(a, a, 90, 90) == 0.0

    def test_offset_squares(self):
        # Same square shifted 3 columns east: every point moves 3 cells = 270 m
        a = box_mask((20, 20), 5, 10, 5, 10)
        b = box_mask((20, 20), 5, 10, 8, 13)
        assert hausdorff_m(a, b, 90, 90) == pytest.approx(270.0)

    def test_nested_squares_uses_far_corner(self):
        # A small square in one corner of a big one: farthest point of B from A is the opposite
        # corner, 9 cells east and 9 cells south
        a = box_mask((10, 10), 0, 1, 0, 1)
        b = box_mask((10, 10), 0, 10, 0, 10)
        assert hausdorff_m(a, b, 10, 20) == pytest.approx(math.hypot(9 * 10, 9 * 20))

    def test_anisotropic_cells(self):
        a = box_mask((10, 10), 0, 1, 0, 1)
        b = box_mask((10, 10), 4, 5, 0, 1)
        assert hausdorff_m(a, b, dx=50, dy=100) == pytest.approx(400.0)

    def test_empty_is_nan(self):
        a = box_mask((5, 5), 1, 2, 1, 2)
        assert math.isnan(hausdorff_m(a, np.zeros_like(a), 1, 1))

    def test_polygon_hausdorff_offset_squares(self):
        a = shapely.box(0, 0, 100, 100)
        b = shapely.box(30, 0, 130, 100)
        assert polygon_hausdorff_m(a, b) == pytest.approx(30.0)


class TestHeadSpread:
    def test_eastward_growth(self):
        initial = box_mask((20, 40), 8, 12, 5, 10)
        growth = box_mask((20, 40), 8, 12, 10, 25)  # 15 cells further east
        h = head_spread(initial, growth, dx=100, dy=100)
        assert h.distance_m == pytest.approx(1500.0)
        assert h.bearing_deg == pytest.approx(90.0)

    def test_northward_growth(self):
        initial = box_mask((30, 20), 20, 25, 8, 12)
        growth = box_mask((30, 20), 10, 20, 8, 12)
        h = head_spread(initial, growth, dx=50, dy=80)
        assert h.distance_m == pytest.approx(800.0)
        assert h.bearing_deg == pytest.approx(0.0)

    def test_none_without_growth(self):
        initial = box_mask((5, 5), 1, 2, 1, 2)
        assert head_spread(initial, initial, 1, 1) is None


def test_bearing_error_wraps():
    assert bearing_error_deg(10, 350) == pytest.approx(20)
    assert bearing_error_deg(350, 10) == pytest.approx(-20)
    assert bearing_error_deg(180, 0) == pytest.approx(-180)


def test_cruz_alexander_band():
    assert within_cruz_alexander(1.3, 1.0) is True
    assert within_cruz_alexander(0.66, 1.0) is True
    assert within_cruz_alexander(1.36, 1.0) is False
    assert within_cruz_alexander(1.0, 0.0) is None
