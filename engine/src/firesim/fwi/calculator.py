"""Canadian Fire Weather Index (FWI) System Calculator.

Complete implementation based on:
    Forestry Canada Fire Danger Group (1992).
    Van Wagner, C.E. and Pickett, T.L. (1985).
    Equations and FORTRAN program for the Canadian Forest Fire Weather Index System.

Calculates all six FWI components from standard noon weather observations.
"""

from __future__ import annotations

import math

from firesim.types import FWIResult

# FFMC moisture-content scale coefficient (Van Wagner 1987, FTR-35, eqs 2a/2b, printed p. 4,
# PDF p. 15; ST-X-3 eq 46, printed p. 30). The printed value 147.2 is used everywhere in
# FireSim (engine and frontend). cffdrs uses the exact 250 * 59.5 / 101 = 147.27723. For a given
# FFMC the ISI moves by at most 0.1 %, but through the daily FFMC recursion the codes differ by up
# to 0.12 FFMC, 0.26 ISI and 0.25 FWI on the cffdrs 48-day sequence and by up to 0.67 ISI /
# 0.52 FWI on single days near FFMC 97 (engine/tests/fwi/test_cffdrs_fwi_reference.py;
# docs/PROJECT_RECORD.md section 4.1).
FFMC_COEFFICIENT = 147.2


# Day length factors for DMC calculation by month.
# Values for ~46°N latitude (standard FWI tables).
# Index 0 is unused (months are 1-12).
_DMC_DAY_LENGTH = [0.0, 6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0]

# Day length factors for DC calculation by month.
_DC_DAY_LENGTH = [0.0, -1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6]


class FWICalculator:
    """Canadian Fire Weather Index System calculator.

    Maintains state (previous day's FFMC, DMC, DC) for sequential daily
    calculations. Call calculate_daily() for each day in sequence.
    """

    def __init__(
        self,
        ffmc_prev: float = 85.0,
        dmc_prev: float = 6.0,
        dc_prev: float = 15.0,
    ):
        """Initialize with spring startup defaults or custom values.

        Args:
            ffmc_prev: Previous day's FFMC (default: 85.0 spring startup)
            dmc_prev: Previous day's DMC (default: 6.0 spring startup)
            dc_prev: Previous day's DC (default: 15.0 spring startup)
        """
        self.ffmc_prev = ffmc_prev
        self.dmc_prev = dmc_prev
        self.dc_prev = dc_prev

    def calculate_ffmc(
        self, temp: float, rh: float, wind: float, rain: float, ffmc_prev: float
    ) -> float:
        """Calculate Fine Fuel Moisture Code.

        Represents moisture content of surface litter (top 1-2 cm).
        Time lag: 2/3 day.

        Args:
            temp: Noon temperature (Celsius)
            rh: Noon relative humidity (%)
            wind: Noon wind speed at 10m (km/h)
            rain: 24-hour rainfall (mm)
            ffmc_prev: Previous day's FFMC

        Returns:
            FFMC value (0-101 scale)
        """
        mo = FFMC_COEFFICIENT * (101.0 - ffmc_prev) / (59.5 + ffmc_prev)

        # Rain adjustment
        if rain > 0.5:
            rf = rain - 0.5
            if mo <= 150.0:
                mr = mo + 42.5 * rf * math.exp(-100.0 / (251.0 - mo)) * (
                    1.0 - math.exp(-6.93 / rf)
                )
            else:
                mr = (
                    mo
                    + 42.5 * rf * math.exp(-100.0 / (251.0 - mo))
                    * (1.0 - math.exp(-6.93 / rf))
                    + 0.0015 * (mo - 150.0) ** 2 * math.sqrt(rf)
                )
            mr = min(mr, 250.0)
            mo = mr

        # Equilibrium moisture content for drying
        ed = (
            0.942 * rh**0.679
            + 11.0 * math.exp((rh - 100.0) / 10.0)
            + 0.18 * (21.1 - temp) * (1.0 - 1.0 / math.exp(0.115 * rh))
        )

        if mo > ed:
            # Drying
            ko = 0.424 * (1.0 - (rh / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (
                1.0 - (rh / 100.0) ** 8
            )
            kd = ko * 0.581 * math.exp(0.0365 * temp)
            m = ed + (mo - ed) * 10.0 ** (-kd)
        else:
            # Wetting
            ew = (
                0.618 * rh**0.753
                + 10.0 * math.exp((rh - 100.0) / 10.0)
                + 0.18 * (21.1 - temp) * (1.0 - 1.0 / math.exp(0.115 * rh))
            )
            if mo < ew:
                kl = 0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7) + 0.0694 * math.sqrt(
                    wind
                ) * (1.0 - ((100.0 - rh) / 100.0) ** 8)
                kw = kl * 0.581 * math.exp(0.0365 * temp)
                m = ew - (ew - mo) * 10.0 ** (-kw)
            else:
                m = mo

        ffmc = 59.5 * (250.0 - m) / (FFMC_COEFFICIENT + m)
        return max(0.0, min(101.0, ffmc))

    def calculate_dmc(
        self, temp: float, rh: float, rain: float, month: int, dmc_prev: float
    ) -> float:
        """Calculate Duff Moisture Code.

        Represents moisture of loosely compacted organic layers (7-10 cm).
        Time lag: ~15 days.

        Args:
            temp: Noon temperature (Celsius)
            rh: Noon relative humidity (%)
            rain: 24-hour rainfall (mm)
            month: Month (1-12)
            dmc_prev: Previous day's DMC

        Returns:
            DMC value (0+ scale)
        """
        if rain > 1.5:
            re = 0.92 * rain - 1.27
            mo = 20.0 + math.exp(5.6348 - dmc_prev / 43.43)

            if dmc_prev <= 33.0:
                b = 100.0 / (0.5 + 0.3 * dmc_prev)
            elif dmc_prev <= 65.0:
                b = 14.0 - 1.3 * math.log(dmc_prev)
            else:
                b = 6.2 * math.log(dmc_prev) - 17.2

            mr = mo + 1000.0 * re / (48.77 + b * re)
            pr = 244.72 - 43.43 * math.log(mr - 20.0)
            dmc_prev = max(0.0, pr)

        dl = _DMC_DAY_LENGTH[month]

        if temp > -1.1:
            # Van Wagner & Pickett (1985) Eq. 17-18: drying rate k, then Ps = Po + k
            # Note: the 1e-4 factor is already included in the coefficient — do NOT
            # multiply k by 100 (that was a transcription error giving 100x inflation).
            k = 1.894 * (temp + 1.1) * (100.0 - rh) * dl * 1e-4
            dmc = dmc_prev + k
        else:
            dmc = dmc_prev

        return max(0.0, dmc)

    def calculate_dc(
        self, temp: float, rain: float, month: int, dc_prev: float
    ) -> float:
        """Calculate Drought Code.

        Represents moisture of deep compact organic layers (10-20 cm).
        Time lag: ~52 days.

        Args:
            temp: Noon temperature (Celsius)
            rain: 24-hour rainfall (mm)
            month: Month (1-12)
            dc_prev: Previous day's DC

        Returns:
            DC value (0+ scale)
        """
        if rain > 2.8:
            rd = 0.83 * rain - 1.27
            qo = 800.0 * math.exp(-dc_prev / 400.0)
            qr = qo + 3.937 * rd
            dr = 400.0 * math.log(800.0 / qr)
            dc_prev = max(0.0, dr)

        lf = _DC_DAY_LENGTH[month]

        # Potential evapotranspiration V = 0.36 (T + 2.8) + Lf, halved and added (Van Wagner
        # 1987, FTR-35, eqs 25-26, printed p. 14, PDF p. 25; Lf from Table 3, printed p. 15).
        # Below -2.8 C the temperature is floored at -2.8, so the day-length term still counts
        # (V = Lf); a negative V adds nothing. This is the Van Wagner & Pickett (1985) program
        # rule as carried by cffdrs (R drought_code.r, Python fwi.drought_code); FTR-33 itself
        # is not on disk. Before 2026-10-10 FireSim added nothing at T <= -2.8 C, which lost
        # 0.45-3.2 DC per cold day in April-October.
        t = max(temp, -2.8)
        v = max(0.0, 0.36 * (t + 2.8) + lf)
        dc = dc_prev + 0.5 * v

        return max(0.0, dc)

    @staticmethod
    def calculate_isi(ffmc: float, wind: float) -> float:
        """Calculate Initial Spread Index.

        Combines FFMC and wind to represent fire spread potential.

        Args:
            ffmc: Fine Fuel Moisture Code
            wind: Wind speed at 10m (km/h)

        Returns:
            ISI value (0+ scale)
        """
        m = FFMC_COEFFICIENT * (101.0 - ffmc) / (59.5 + ffmc)
        ff = 91.9 * math.exp(-0.1386 * m) * (1.0 + m**5.31 / 4.93e7)
        fw = math.exp(0.05039 * wind)
        return 0.208 * fw * ff

    @staticmethod
    def calculate_bui(dmc: float, dc: float) -> float:
        """Calculate Buildup Index.

        Combines DMC and DC to represent total fuel available.

        Args:
            dmc: Duff Moisture Code
            dc: Drought Code

        Returns:
            BUI value (0+ scale)
        """
        if dmc == 0.0 and dc == 0.0:
            return 0.0
        if dmc <= 0.4 * dc:
            bui = 0.8 * dmc * dc / (dmc + 0.4 * dc)
        else:
            bui = dmc - (1.0 - 0.8 * dc / (dmc + 0.4 * dc)) * (0.92 + (0.0114 * dmc) ** 1.7)
        return max(0.0, bui)

    @staticmethod
    def calculate_fwi(isi: float, bui: float) -> float:
        """Calculate Fire Weather Index.

        Overall fire danger rating combining spread potential and fuel available.

        Args:
            isi: Initial Spread Index
            bui: Buildup Index

        Returns:
            FWI value (0+ scale)
        """
        if bui <= 80.0:
            fd = 0.626 * bui**0.809 + 2.0
        else:
            fd = 1000.0 / (25.0 + 108.64 * math.exp(-0.023 * bui))

        b = 0.1 * isi * fd

        if b <= 1.0:
            return b
        return math.exp(2.72 * (0.434 * math.log(b)) ** 0.647)

    def calculate_daily(
        self, temp: float, rh: float, wind: float, rain: float, month: int
    ) -> FWIResult:
        """Calculate all FWI components for one day and update state.

        This is the main entry point for daily FWI calculations.
        Call sequentially for each day — the calculator maintains
        previous-day values internally.

        Args:
            temp: Noon temperature (Celsius)
            rh: Noon relative humidity (%)
            wind: Noon wind speed at 10m (km/h)
            rain: 24-hour rainfall (mm)
            month: Month (1-12)

        Returns:
            FWIResult with all six components
        """
        ffmc = self.calculate_ffmc(temp, rh, wind, rain, self.ffmc_prev)
        dmc = self.calculate_dmc(temp, rh, rain, month, self.dmc_prev)
        dc = self.calculate_dc(temp, rain, month, self.dc_prev)
        isi = self.calculate_isi(ffmc, wind)
        bui = self.calculate_bui(dmc, dc)
        fwi = self.calculate_fwi(isi, bui)

        # Update state for next day
        self.ffmc_prev = ffmc
        self.dmc_prev = dmc
        self.dc_prev = dc

        return FWIResult(ffmc=ffmc, dmc=dmc, dc=dc, isi=isi, bui=bui, fwi=fwi)

    def reset(
        self,
        ffmc: float = 85.0,
        dmc: float = 6.0,
        dc: float = 15.0,
    ) -> None:
        """Reset to spring startup values or custom values."""
        self.ffmc_prev = ffmc
        self.dmc_prev = dmc
        self.dc_prev = dc


def hourly_ffmc(
    temp: float, rh: float, wind: float, rain: float, ffmc_prev: float, hours: float = 1.0
) -> float:
    """Hourly Fine Fuel Moisture Code (Van Wagner 1977, as revised in cffdrs ``hffmc``).

    FFMC after ``hours`` of constant weather, from the FFMC at the start of the period.
    Drying and wetting rates are those of the hourly model (a pine-needle litter layer), not
    the daily FFMC's; rain is the amount in the period (mm). Uses the ST-X-3 / FWI moisture
    coefficient ``FFMC_COEFFICIENT`` (147.2) like the rest of FireSim (cffdrs uses 147.27723).

    Args:
        temp: Temperature (C)
        rh: Relative humidity (%)
        wind: 10 m wind speed (km/h)
        rain: Rain in the period (mm)
        ffmc_prev: FFMC at the start of the period
        hours: Length of the period (h)
    """
    rh = min(max(rh, 0.0), 100.0)
    mo = FFMC_COEFFICIENT * (101.0 - ffmc_prev) / (59.5 + ffmc_prev)
    if rain > 0.0:
        mr = mo + 42.5 * rain * math.exp(-100.0 / (251.0 - mo)) * (1.0 - math.exp(-6.93 / rain))
        if mo > 150.0:
            mr += 0.0015 * (mo - 150.0) ** 2 * rain**0.5
        mo = min(mr, 250.0)
    ed = 0.942 * rh**0.679 + 11.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (
        1.0 - math.exp(-0.115 * rh)
    )
    ew = 0.618 * rh**0.753 + 10.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (
        1.0 - math.exp(-0.115 * rh)
    )
    if mo > ed:
        ko = 0.424 * (1.0 - (rh / 100.0) ** 1.7) + 0.0694 * wind**0.5 * (1.0 - (rh / 100.0) ** 8)
        kd = ko * 0.0579 * math.exp(0.0365 * temp)
        m = ed + (mo - ed) * 10.0 ** (-kd * hours)
    elif mo < ew:
        k1 = 0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7) + 0.0694 * wind**0.5 * (
            1.0 - ((100.0 - rh) / 100.0) ** 8
        )
        kw = k1 * 0.0579 * math.exp(0.0365 * temp)
        m = ew - (ew - mo) * 10.0 ** (-kw * hours)
    else:
        m = mo
    return max(59.5 * (250.0 - m) / (FFMC_COEFFICIENT + m), 0.0)
