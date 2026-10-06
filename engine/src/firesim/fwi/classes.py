"""FWI display classes.

The intervals of the CWFIS national Fire Weather Index map legend (public:fwi layer,
GeoServer GetLegendGraphic, checked 2026-06-10): 0-5 Low, 6-15 Moderate, 16-22 High,
23-29 Very High, 30+ Extreme. Values are rounded to the nearest whole number before
classing, as the legend's integer intervals imply.

This is an FWI map classification, not an official fire danger rating: provincial danger
ratings are issued by the agencies (e.g. Alberta Wildfire) from their own criteria.
"""

FWI_CLASS_LIMITS: tuple[tuple[float, str], ...] = (
    (5.5, "Low"),
    (15.5, "Moderate"),
    (22.5, "High"),
    (29.5, "Very High"),
)


def fwi_class(fwi: float) -> str:
    """CWFIS FWI map class for an FWI value."""
    for upper, label in FWI_CLASS_LIMITS:
        if fwi < upper:
            return label
    return "Extreme"
