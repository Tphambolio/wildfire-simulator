"""Fire ellipse geometry for Huygens wavelet spread.

Calculates fire ellipse shape parameters from FBP rate of spread
and wind speed. Used by the Huygens spread module to determine
the shape and orientation of fire spread wavelets.

References:
    - Forestry Canada Fire Danger Group (1992). ST-X-3, eqs 79-89.
    - Wotton, B.M. et al. (2009). GLC-X-10 (grass LB, ROS at theta).
    - Unverified: an "Anderson, K. et al. (2009) Prometheus fire growth model" reference was
      listed here; no such publication was identified. Prometheus is documented by Tymstra et
      al. (2010), Information Report NOR-X-417 (not checked against this module). The
      equations here are those of ST-X-3 and GLC-X-10.
"""

from __future__ import annotations

import math

from firesim.fbp.calculator import calculate_length_to_breadth
from firesim.fbp.constants import FuelType


def calculate_length_to_breadth_ratio(
    wind_speed: float, fuel_type: FuelType | str | None = None
) -> float:
    """Calculate fire ellipse length-to-breadth ratio from wind speed.

    Forest fuels (ST-X-3 eq 79): LB = 1 + 8.729 * (1 - exp(-0.030 * WSV))^2.155
    Grass, O-1a/O-1b (Wotton et al. 2009 eq 80): LB = 1.1 * WSV^0.464

    Args:
        wind_speed: Net effective wind speed (km/h)
        fuel_type: FBP fuel type (None = forest equation)

    Returns:
        Length-to-breadth ratio (>=1.0).
    """
    return calculate_length_to_breadth(fuel_type, wind_speed)


def calculate_eccentricity(lbr: float) -> float:
    """Calculate fire ellipse eccentricity from length-to-breadth ratio.

    e = sqrt(1 - 1/LBR^2)

    Args:
        lbr: Length-to-breadth ratio (>=1.0)

    Returns:
        Eccentricity (0.0 to <1.0). 0 = circle, approaching 1 = very elongated.
    """
    if lbr <= 1.0:
        return 0.0
    return math.sqrt(1.0 - 1.0 / (lbr * lbr))


def calculate_flank_ros(head_ros: float, back_ros: float, lbr: float) -> float:
    """Calculate flank fire rate of spread (ST-X-3 eq 89).

    FROS = (ROS + BROS) / (2 * LB), i.e. the ellipse semi-minor axis rate.
    The back rate BROS must come from the FBP back ISI (``FBPResult.back_ros``),
    not from the head rate and LB.

    Args:
        head_ros: Head fire rate of spread (m/min)
        back_ros: Back fire rate of spread (m/min)
        lbr: Length-to-breadth ratio

    Returns:
        Flank ROS (m/min)
    """
    return (head_ros + back_ros) / (2.0 * max(lbr, 1.0))


def calculate_ros_at_theta(head_ros: float, flank_ros: float, back_ros: float, theta: float) -> float:
    """Rate of spread from the ignition point toward angle theta on the FBP fire ellipse.

    The FBP ellipse (ST-X-3 eqs 82-89) has semi-major axis a = (ROS + BROS) / 2,
    semi-minor axis FROS, and its centre displaced c = (ROS - BROS) / 2 toward
    the head, so the ignition point lies inside it but not at a focus. The rate
    toward theta is the distance from the ignition point to that ellipse along
    theta, per unit time:

        r = [FROS^2 c cos + FROS sqrt(FROS^2 c^2 cos^2 + A * ROS * BROS)] / A,
        A = FROS^2 cos^2 + a^2 sin^2   (using a^2 - c^2 = ROS * BROS)

    (The closed form in Wotton et al. 2009, as transcribed in cffdrs
    ``rate_of_spread_at_theta``, does not return BROS at theta = pi.)

    Args:
        head_ros, flank_ros, back_ros: FBP rates (m/min)
        theta: Angle from the head direction (radians)

    Returns:
        ROS toward theta (m/min): ROS at 0, BROS at pi.
    """
    a = (head_ros + back_ros) / 2.0
    c = (head_ros - back_ros) / 2.0
    f = flank_ros
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    big_a = f * f * cos_t * cos_t + a * a * sin_t * sin_t
    if big_a <= 0.0:
        return 0.0
    disc = f * f * c * c * cos_t * cos_t + big_a * head_ros * back_ros
    return (f * f * c * cos_t + f * math.sqrt(max(disc, 0.0))) / big_a


def calculate_ellipse_area(
    head_ros: float, back_ros: float, lbr: float, time_hours: float
) -> float:
    """Calculate fire ellipse area at a given time (ST-X-3 eqs 82-86, no acceleration).

    - semi-major axis a = (head_distance + back_distance) / 2
    - semi-minor axis b = a / LBR

    Args:
        head_ros: Head fire rate of spread (m/min)
        back_ros: Back fire rate of spread (m/min)
        lbr: Length-to-breadth ratio
        time_hours: Time since ignition (hours)

    Returns:
        Fire area in hectares
    """
    time_min = time_hours * 60.0
    a = (head_ros + back_ros) * time_min / 2.0
    b = a / lbr if lbr > 0 else a
    return math.pi * a * b / 10000.0


def generate_ellipse_points(
    center_lat: float,
    center_lng: float,
    head_ros: float,
    back_ros: float,
    lbr: float,
    wind_direction: float,
    time_hours: float,
    num_points: int = 72,
) -> list[tuple[float, float]]:
    """Generate polygon points for the fire ellipse perimeter.

    Creates an ellipse centered on the ignition point, oriented in the
    wind direction, with semi-axes derived from ROS and LBR.

    Args:
        center_lat: Ignition latitude
        center_lng: Ignition longitude
        head_ros: Head fire ROS (m/min)
        back_ros: Back fire ROS (m/min)
        lbr: Length-to-breadth ratio
        wind_direction: Wind direction (degrees, meteorological FROM convention).
            Fire spreads in the opposite direction.
        time_hours: Time since ignition (hours)
        num_points: Number of perimeter vertices

    Returns:
        List of (lat, lng) tuples forming a closed polygon
    """
    time_min = time_hours * 60.0
    head_dist = head_ros * time_min
    back_dist = back_ros * time_min

    # Ellipse semi-axes in meters
    semi_major = (head_dist + back_dist) / 2.0
    semi_minor = semi_major / lbr if lbr > 0 else semi_major

    # Offset from center: fire spreads downwind, so the ellipse center
    # is shifted downwind from the ignition point
    offset = (head_dist - back_dist) / 2.0

    # Fire spread direction (opposite of wind FROM direction)
    spread_dir_rad = math.radians((wind_direction + 180.0) % 360.0)

    # Meters to degrees conversion (approximate)
    lat_per_m = 1.0 / 111320.0
    lng_per_m = 1.0 / (111320.0 * math.cos(math.radians(center_lat)))

    # Center offset
    offset_lat = offset * math.cos(spread_dir_rad) * lat_per_m
    offset_lng = offset * math.sin(spread_dir_rad) * lng_per_m
    cx = center_lat + offset_lat
    cy = center_lng + offset_lng

    # Generate ellipse points
    points = []
    for i in range(num_points):
        theta = 2.0 * math.pi * i / num_points

        # Point on unrotated ellipse
        ex = semi_major * math.cos(theta)
        ey = semi_minor * math.sin(theta)

        # Rotate by spread direction
        rx = ex * math.cos(spread_dir_rad) - ey * math.sin(spread_dir_rad)
        ry = ex * math.sin(spread_dir_rad) + ey * math.cos(spread_dir_rad)

        # Convert to lat/lng
        lat = cx + rx * lat_per_m
        lng = cy + ry * lng_per_m
        points.append((lat, lng))

    # Close the polygon
    points.append(points[0])
    return points
