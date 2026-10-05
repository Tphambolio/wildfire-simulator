"""Tests for fire ellipse geometry."""

import math

import pytest

from firesim.spread.ellipse import (
    calculate_eccentricity,
    calculate_ellipse_area,
    calculate_flank_ros,
    calculate_length_to_breadth_ratio,
    calculate_ros_at_theta,
    generate_ellipse_points,
)


class TestLBR:
    """Test length-to-breadth ratio calculation."""

    def test_zero_wind_circular(self):
        """Zero wind should produce circular fire (LBR = 1)."""
        lbr = calculate_length_to_breadth_ratio(0.0)
        assert lbr == 1.0

    def test_increases_with_wind(self):
        """LBR should increase with wind speed."""
        lbr_low = calculate_length_to_breadth_ratio(10.0)
        lbr_high = calculate_length_to_breadth_ratio(40.0)
        assert lbr_high > lbr_low

    def test_moderate_wind_reasonable(self):
        """LBR at 20 km/h should be between 1 and 5."""
        lbr = calculate_length_to_breadth_ratio(20.0)
        assert 1.0 < lbr < 5.0

    def test_always_at_least_one(self):
        """LBR should never be less than 1."""
        lbr = calculate_length_to_breadth_ratio(-5.0)
        assert lbr >= 1.0


class TestEccentricity:
    """Test fire ellipse eccentricity."""

    def test_circle_zero_eccentricity(self):
        """LBR = 1 (circle) should have eccentricity = 0."""
        assert calculate_eccentricity(1.0) == 0.0

    def test_elongated_high_eccentricity(self):
        """High LBR should have high eccentricity approaching 1."""
        e = calculate_eccentricity(5.0)
        assert 0.9 < e < 1.0

    def test_moderate_eccentricity(self):
        """Moderate LBR should have moderate eccentricity."""
        e = calculate_eccentricity(2.0)
        assert 0.5 < e < 0.95


class TestFlankAndThetaROS:
    """Flank rate (ST-X-3 eq 89) and the rate toward theta on the FBP ellipse."""

    def test_flank_is_semi_minor_rate(self):
        """FROS = (ROS + BROS) / (2 LB)."""
        assert calculate_flank_ros(10.0, 1.0, 2.5) == pytest.approx(11.0 / 5.0)

    def test_flank_between_back_and_head(self):
        flank = calculate_flank_ros(10.0, 0.5, 3.0)
        assert 0.5 < flank < 10.0

    def test_theta_endpoints(self):
        """ROS at 0 is the head rate, at pi the back rate."""
        head, back, lb = 10.0, 0.5, 3.0
        flank = calculate_flank_ros(head, back, lb)
        assert calculate_ros_at_theta(head, flank, back, 0.0) == pytest.approx(head)
        assert calculate_ros_at_theta(head, flank, back, math.pi) == pytest.approx(back, rel=1e-3)

    def test_theta_monotone_from_head_to_back(self):
        head, back, lb = 10.0, 0.5, 3.0
        flank = calculate_flank_ros(head, back, lb)
        rates = [calculate_ros_at_theta(head, flank, back, math.radians(d)) for d in range(0, 181, 15)]
        assert all(a >= b for a, b in zip(rates, rates[1:]))

    def test_theta_point_lies_on_fbp_ellipse(self):
        """The point reached toward theta satisfies the ellipse equation."""
        head, back, lb = 12.0, 0.8, 2.8
        flank = calculate_flank_ros(head, back, lb)
        a, c = (head + back) / 2.0, (head - back) / 2.0
        for deg in (20, 75, 90, 130):
            t = math.radians(deg)
            r = calculate_ros_at_theta(head, flank, back, t)
            x, y = r * math.cos(t), r * math.sin(t)
            assert (x - c) ** 2 / a**2 + y**2 / flank**2 == pytest.approx(1.0)

    def test_grass_lb_equation(self):
        """O-1 fuels use LB = 1.1 WSV^0.464."""
        assert calculate_length_to_breadth_ratio(20.0, "O1a") == pytest.approx(1.1 * 20.0**0.464)


class TestEllipseArea:
    """Test fire ellipse area calculation."""

    def test_area_increases_with_time(self):
        """Area should increase over time."""
        a1 = calculate_ellipse_area(head_ros=5.0, back_ros=0.5, lbr=2.0, time_hours=1.0)
        a2 = calculate_ellipse_area(head_ros=5.0, back_ros=0.5, lbr=2.0, time_hours=2.0)
        assert a2 > a1

    def test_area_increases_with_ros(self):
        """Higher ROS should produce larger area."""
        a_slow = calculate_ellipse_area(head_ros=2.0, back_ros=0.2, lbr=2.0, time_hours=1.0)
        a_fast = calculate_ellipse_area(head_ros=10.0, back_ros=1.0, lbr=2.0, time_hours=1.0)
        assert a_fast > a_slow

    def test_area_positive(self):
        """Area should be positive for any valid inputs."""
        area = calculate_ellipse_area(head_ros=5.0, back_ros=0.5, lbr=2.0, time_hours=1.0)
        assert area > 0.0

    def test_circular_fire_area(self):
        """LBR=1 should produce a circular area: pi * r^2."""
        ros = 5.0  # m/min
        hours = 1.0
        dist = ros * hours * 60  # 300m radius
        expected_ha = math.pi * dist**2 / 10000.0
        actual_ha = calculate_ellipse_area(ros, ros, 1.0, hours)
        assert abs(actual_ha - expected_ha) / expected_ha < 0.01


class TestGenerateEllipsePoints:
    """Test ellipse polygon generation."""

    def test_produces_closed_polygon(self):
        """Generated polygon should be closed (first == last point)."""
        pts = generate_ellipse_points(53.5, -113.5, 5.0, 0.5, 2.0, 225.0, 1.0)
        assert pts[0] == pts[-1]

    def test_correct_number_of_points(self):
        """Should produce num_points + 1 (closed polygon)."""
        pts = generate_ellipse_points(53.5, -113.5, 5.0, 0.5, 2.0, 225.0, 1.0, num_points=36)
        assert len(pts) == 37

    def test_points_near_ignition(self):
        """All points should be within reasonable distance of ignition."""
        lat, lng = 53.5, -113.5
        pts = generate_ellipse_points(lat, lng, 5.0, 0.5, 2.0, 225.0, 1.0)
        for plat, plng in pts:
            assert abs(plat - lat) < 0.1  # Within ~11 km
            assert abs(plng - lng) < 0.2
