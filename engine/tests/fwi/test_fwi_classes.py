"""FWI display classes: CWFIS national FWI map intervals (0-5, 6-15, 16-22, 23-29, 30+)."""

import pytest

from firesim.fwi.classes import fwi_class


@pytest.mark.parametrize(
    "fwi, label",
    [
        (0.0, "Low"), (5.4, "Low"), (5.5, "Moderate"), (15.4, "Moderate"),
        (15.5, "High"), (22.4, "High"), (22.5, "Very High"), (29.4, "Very High"),
        (29.5, "Extreme"), (83.0, "Extreme"),
    ],
)
def test_cwfis_map_intervals(fwi, label):
    assert fwi_class(fwi) == label


def test_same_value_same_class():
    # The audit found FWI 14.8 labelled "High" and 14.7 "Moderate" on one screen
    assert fwi_class(14.8) == fwi_class(14.7) == "Moderate"
