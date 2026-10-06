"""Canadian Fire Behavior Prediction (FBP) System Calculator.

Implements equations from:
    Forestry Canada Fire Danger Group (1992).
    Development and Structure of the Canadian Forest Fire Behavior
    Prediction System. Information Report ST-X-3.
    Wotton, B.M., Alexander, M.E., Taylor, S.W. (2009). Updates and revisions
    to the 1992 Canadian forest fire behavior prediction system. GLC-X-10.

The structure follows the reference implementation (cffdrs, R and Python).
Outputs agree with cffdrs to floating-point precision when cffdrs is run with
the ST-X-3 FFMC coefficient (147.2); see engine/tests/fbp/test_cffdrs_reference.py.
"""

from __future__ import annotations

import math

from firesim.fbp.constants import FuelType, FuelTypeSpec, FUEL_TYPES, get_fuel_spec
from firesim.fbp.crown_fire import (
    calculate_critical_surface_intensity,
    calculate_critical_surface_ros,
    calculate_crown_fraction_burned,
    calculate_crown_ros_c6,
    classify_fire_type,
)
from firesim.types import FBPResult

# FFMC -> moisture coefficient, ST-X-3 eq 46. cffdrs uses the exact value
# 250 * 59.5 / 101 = 147.27723; the difference moves ISI by at most 1e-3.
FFMC_COEF = 147.2

# Low heat of combustion 18,000 kJ/kg over 60 s/min (ST-X-3 eq 69: HFI = 300 * TFC * ROS)
_INTENSITY_FACTOR = 300.0

_GRASS = (FuelType.O1a, FuelType.O1b)
_NO_CROWN_FMC = (FuelType.D1, FuelType.D2, FuelType.S1, FuelType.S2, FuelType.S3,
                 FuelType.O1a, FuelType.O1b)


def _fine_fuel_moisture_function(ffmc: float) -> float:
    """FFMC moisture function f(F) (ST-X-3 eqs 46, 52)."""
    m = FFMC_COEF * (101.0 - ffmc) / (59.5 + ffmc)
    return 91.9 * math.exp(-0.1386 * m) * (1.0 + m**5.31 / 4.93e7)


def calculate_isi(ffmc: float, wind_speed: float) -> float:
    """Initial Spread Index for the FBP System (ST-X-3 eqs 52, 53, 53a).

    Above 40 km/h the wind function is the FBP modification (eq 53a),
    f(W) = 12 * (1 - exp(-0.0818 * (WS - 28))), which caps runaway ISI.

    Args:
        ffmc: Fine Fuel Moisture Code (0-101)
        wind_speed: 10-m open (or net effective) wind speed (km/h)

    Returns:
        ISI value (dimensionless)
    """
    if wind_speed >= 40.0:
        f_w = 12.0 * (1.0 - math.exp(-0.0818 * (wind_speed - 28.0)))
    else:
        f_w = math.exp(0.05039 * wind_speed)
    return 0.208 * f_w * _fine_fuel_moisture_function(ffmc)


def calculate_back_isi(ffmc: float, wsv: float) -> float:
    """Back-fire ISI (ST-X-3 eqs 75-76): wind function exp(-0.05039 * WSV)."""
    return 0.208 * math.exp(-0.05039 * wsv) * _fine_fuel_moisture_function(ffmc)


def calculate_bui(dmc: float, dc: float) -> float:
    """Calculate Buildup Index from DMC and DC.

    Args:
        dmc: Duff Moisture Code
        dc: Drought Code

    Returns:
        BUI value (dimensionless)
    """
    if dmc == 0.0 and dc == 0.0:
        return 0.0
    if dmc <= 0.4 * dc:
        bui = 0.8 * dmc * dc / (dmc + 0.4 * dc)
    else:
        bui = dmc - (1.0 - 0.8 * dc / (dmc + 0.4 * dc)) * (0.92 + (0.0114 * dmc) ** 1.7)
    return max(0.0, bui)


def calculate_bui_effect(bui: float, q: float, bui0: float) -> float:
    """Calculate BUI effect on rate of spread (ST-X-3 eq 54).

    BE = exp(50 * ln(q) * (1/BUI - 1/BUI0))

    Args:
        bui: Buildup Index (<= 0 disables the effect)
        q: BUI effect parameter (fuel-type specific)
        bui0: BUI threshold parameter (fuel-type specific)

    Returns:
        BUI effect multiplier (dimensionless)
    """
    if bui <= 0.0 or bui0 <= 0.0:
        return 1.0
    return math.exp(50.0 * math.log(q) * (1.0 / bui - 1.0 / bui0))


def _fuel_bui_effect(spec: FuelTypeSpec, bui: float | None) -> float:
    """Buildup effect for a fuel type; ``bui=None`` disables it.

    D-2 (green aspen) does not carry fire below BUI 80 (Wotton et al. 2009).
    """
    if bui is None:
        return 1.0
    if spec.code == FuelType.D2 and 0.0 <= bui < 80.0:
        return 0.0
    return calculate_bui_effect(bui, spec.q, spec.bui0)


def calculate_grass_curing_factor(grass_cure: float) -> float:
    """Grass curing factor for O-1a/O-1b (Wotton et al. 2009, eqs 35a/35b).

    CF = 0.005 * (exp(0.061 * C) - 1)    for C < 58.8
    CF = 0.176 + 0.02 * (C - 58.8)       for C >= 58.8

    CF reaches 1.0 at 100 % cured. (The 1992 form, which had an extra
    quadratic term, gave about 0.71 at full cure.)

    Args:
        grass_cure: Degree of curing (0-100). 0=green, 100=fully cured.

    Returns:
        Curing factor (0.0 to 1.0)
    """
    cc = float(grass_cure)
    if cc < 58.8:
        return 0.005 * (math.exp(0.061 * cc) - 1.0)
    return 0.176 + 0.02 * (cc - 58.8)


def _basic_rsi(spec: FuelTypeSpec, isi: float) -> float:
    """RSI = a * (1 - exp(-b * ISI))^c (ST-X-3 eq 26)."""
    return spec.a * (1.0 - math.exp(-spec.b * isi)) ** spec.c


def _floored_rsi(code: FuelType, isi: float) -> float:
    rsi = _basic_rsi(FUEL_TYPES[code], isi)
    return rsi if rsi > 0.0 else 0.000001


def calculate_rsi(
    spec: FuelTypeSpec,
    isi: float,
    pc: float = 50.0,
    grass_cure: float = 60.0,
    pdf: float = 35.0,
) -> float:
    """Initial rate of spread before the buildup effect (m/min).

    Handles the fuel-type specific forms: D-2 at 0.2 x D-1 (Wotton 2009),
    M-1/M-2 percent-conifer blends (ST-X-3 eqs 27-28), M-3/M-4 percent
    dead fir blends (eqs 29-33, Wotton 2009 M-4 c), and the O-1 curing
    factor (Wotton 2009 eq 35).
    """
    fuel = spec.code
    if fuel == FuelType.M1:
        return pc / 100.0 * _floored_rsi(FuelType.C2, isi) + (100.0 - pc) / 100.0 * _floored_rsi(
            FuelType.D1, isi
        )
    if fuel == FuelType.M2:
        return pc / 100.0 * _floored_rsi(FuelType.C2, isi) + 0.2 * (
            100.0 - pc
        ) / 100.0 * _floored_rsi(FuelType.D1, isi)
    if fuel == FuelType.M3:
        return pdf / 100.0 * _basic_rsi(spec, isi) + (1.0 - pdf / 100.0) * _floored_rsi(
            FuelType.D1, isi
        )
    if fuel == FuelType.M4:
        return pdf / 100.0 * _basic_rsi(spec, isi) + 0.2 * (1.0 - pdf / 100.0) * _floored_rsi(
            FuelType.D1, isi
        )
    if fuel in _GRASS:
        return _basic_rsi(spec, isi) * calculate_grass_curing_factor(grass_cure)
    if fuel == FuelType.D2:
        return 0.2 * _basic_rsi(spec, isi)
    return _basic_rsi(spec, isi)


def calculate_surface_ros(
    spec: FuelTypeSpec,
    isi: float,
    bui: float | None,
    pc: float = 50.0,
    grass_cure: float = 60.0,
    pdf: float = 35.0,
) -> float:
    """Surface rate of spread RSS = RSI * BE (m/min).

    The buildup effect applies to every fuel type with q < 1 (all except
    O-1), including D-1 and the M types. ``bui=None`` disables it.

    Args:
        spec: Fuel type specification (from get_fuel_spec).
        isi: Initial Spread Index — dimensionless.
        bui: Buildup Index, or None to disable the buildup effect.
        pc: Percent conifer (0–100, M1/M2 types only).
        grass_cure: Percent curing (0–100, O1a/O1b types only).
        pdf: Percent dead balsam fir (0–100, M3/M4 types only).

    Returns:
        Surface rate of spread (m/min). Non-negative.
    """
    return max(0.0, calculate_rsi(spec, isi, pc, grass_cure, pdf) * _fuel_bui_effect(spec, bui))


def calculate_sfc(
    spec: FuelTypeSpec,
    ffmc: float,
    bui: float,
    pc: float = 50.0,
    gfl: float = 0.35,
) -> float:
    """Surface fuel consumption (kg/m2), ST-X-3 eqs 9-25 with Wotton 2009 C-1.

    Args:
        spec: Fuel type specification
        ffmc: Fine Fuel Moisture Code
        bui: Buildup Index
        pc: Percent conifer (M-1/M-2)
        gfl: Grass fuel load (kg/m2, O-1a/O-1b)

    Returns:
        SFC (kg/m2), floored at 1e-6 as in cffdrs (D-2 below BUI 80 is 0).
    """
    fuel = spec.code
    if fuel == FuelType.C1:
        if ffmc > 84.0:
            sfc = 0.75 + 0.75 * (1.0 - math.exp(-0.23 * (ffmc - 84.0))) ** 0.5
        else:
            sfc = 0.75 - 0.75 * (1.0 - math.exp(-0.23 * (84.0 - ffmc))) ** 0.5
    elif fuel in (FuelType.C2, FuelType.M3, FuelType.M4):
        sfc = 5.0 * (1.0 - math.exp(-0.0115 * bui))
    elif fuel in (FuelType.C3, FuelType.C4):
        sfc = 5.0 * (1.0 - math.exp(-0.0164 * bui)) ** 2.24
    elif fuel in (FuelType.C5, FuelType.C6):
        sfc = 5.0 * (1.0 - math.exp(-0.0149 * bui)) ** 2.48
    elif fuel == FuelType.C7:
        sfc = 2.0 * (1.0 - math.exp(-0.104 * (ffmc - 70.0))) if ffmc > 70.0 else 0.0
        sfc += 1.5 * (1.0 - math.exp(-0.0201 * bui))
    elif fuel == FuelType.D1:
        sfc = 1.5 * (1.0 - math.exp(-0.0183 * bui))
    elif fuel == FuelType.D2:
        if bui < 80.0:
            return 0.0  # green aspen does not burn below BUI 80 (Wotton 2009)
        sfc = 1.5 * (1.0 - math.exp(-0.0183 * bui))
    elif fuel in (FuelType.M1, FuelType.M2):
        sfc = pc / 100.0 * 5.0 * (1.0 - math.exp(-0.0115 * bui)) + (
            100.0 - pc
        ) / 100.0 * 1.5 * (1.0 - math.exp(-0.0183 * bui))
    elif fuel in _GRASS:
        sfc = gfl
    elif fuel == FuelType.S1:
        sfc = 4.0 * (1.0 - math.exp(-0.025 * bui)) + 4.0 * (1.0 - math.exp(-0.034 * bui))
    elif fuel == FuelType.S2:
        sfc = 10.0 * (1.0 - math.exp(-0.013 * bui)) + 6.0 * (1.0 - math.exp(-0.060 * bui))
    elif fuel == FuelType.S3:
        sfc = 12.0 * (1.0 - math.exp(-0.0166 * bui)) + 20.0 * (1.0 - math.exp(-0.0210 * bui))
    else:
        sfc = 0.0
    return sfc if sfc > 0.0 else 0.000001


def calculate_length_to_breadth(fuel_type: FuelType | str | None, wsv: float) -> float:
    """Length-to-breadth ratio (ST-X-3 eq 79; grass eq 80 per Wotton 2009).

    Forest:  LB = 1 + 8.729 * (1 - exp(-0.030 * WSV))^2.155
    Grass:   LB = 1.1 * WSV^0.464 (WSV >= 1), else 1.0

    Args:
        fuel_type: FBP fuel type (None = forest equation)
        wsv: Net effective wind speed (km/h)
    """
    if fuel_type is not None and FuelType(fuel_type) in _GRASS:
        return 1.1 * wsv**0.464 if wsv >= 1.0 else 1.0
    if wsv <= 0.0:
        return 1.0
    return 1.0 + 8.729 * (1.0 - math.exp(-0.030 * wsv)) ** 2.155


def calculate_foliar_moisture(
    lat: float, lng: float, elevation_m: float | None, day_of_year: int
) -> float:
    """Foliar moisture content (%) from location and date (ST-X-3 eqs 1-8).

    Args:
        lat: Latitude (degrees N)
        lng: Longitude (degrees; west may be negative)
        elevation_m: Elevation (m), or None/<=0 to use the sea-level form
        day_of_year: Julian day (1-366)
    """
    lon = abs(lng)
    elv = elevation_m if elevation_m is not None else 0.0
    if elv <= 0.0:
        latn = 46.0 + 23.4 * math.exp(-0.0360 * (150.0 - lon))
        d0 = 151.0 * (lat / latn)
    else:
        latn = 43.0 + 33.7 * math.exp(-0.0351 * (150.0 - lon))
        d0 = 142.1 * (lat / latn) + 0.0172 * elv
    d0 = round(d0)
    nd = abs(day_of_year - d0)
    if nd < 30:
        return 85.0 + 0.0189 * nd**2
    if nd < 50:
        return 32.9 + 3.17 * nd - 0.0288 * nd**2
    return 120.0


def _rate_of_spread(
    spec: FuelTypeSpec,
    isi: float,
    bui: float | None,
    fmc: float,
    sfc: float,
    pc: float,
    pdf: float,
    cc: float,
    cbh: float,
) -> tuple[float, float, float, float, float]:
    """Final ROS with crown involvement (ST-X-3 eqs 26-66).

    Returns:
        (ros, rss, cfb, csi, rso). For all fuel types except C-6 the final
        rate equals the surface rate RSS; crowning raises intensity through
        crown fuel consumption, not speed. C-6 uses its own crown rate RSC.
    """
    rss = calculate_surface_ros(spec, isi, bui, pc, cc, pdf)
    csi = calculate_critical_surface_intensity(cbh, fmc)
    rso = calculate_critical_surface_ros(csi, sfc)
    if spec.code == FuelType.C6:
        rsc = calculate_crown_ros_c6(isi, fmc)
        cfb = calculate_crown_fraction_burned(rss, rso) if rsc > rss else 0.0
        ros = rss + cfb * (rsc - rss) if rsc > rss else rss
    else:
        cfb = calculate_crown_fraction_burned(rss, rso)
        ros = rss
    if ros <= 0.0:
        ros = 0.000001
    return ros, rss, cfb, csi, rso


def _isf_from_rsf(spec: FuelTypeSpec, rsf: float, cf: float = 1.0) -> float:
    """Invert the basic ROS equation for the slope-equivalent ISI (ST-X-3 eq 41)."""
    a = spec.a * (0.2 if spec.code == FuelType.D2 else 1.0) * cf
    if a <= 0.0:
        return 0.0
    temp = 1.0 - (rsf / a) ** (1.0 / spec.c)
    return math.log(max(temp, 0.01)) / (-spec.b)


def calculate_slope_adjustment(
    spec: FuelTypeSpec,
    ffmc: float,
    wind_speed: float,
    wind_to_deg: float,
    slope_pct: float,
    upslope_deg: float,
    fmc: float,
    sfc: float,
    pc: float,
    pdf: float,
    cc: float,
    cbh: float,
) -> tuple[float, float]:
    """Net effective wind speed and spread direction (ST-X-3 eqs 39-50).

    The slope effect is converted to an equivalent wind speed WSE, then
    added vectorially to the wind to give WSV and RAZ.

    Args:
        wind_to_deg: Direction the wind blows toward (degrees, 0=N)
        slope_pct: Ground slope (%)
        upslope_deg: Upslope azimuth (degrees, 0=N)

    Returns:
        (wsv km/h, raz degrees toward, 0=N). Falls back to the wind alone
        when the slope-equivalent ISI is undefined.
    """
    sf = 10.0 if slope_pct >= 70.0 else math.exp(3.533 * (slope_pct / 100.0) ** 1.2)
    isz = calculate_isi(ffmc, 0.0)

    def rsz(s: FuelTypeSpec, p: float) -> float:
        return _rate_of_spread(s, isz, None, fmc, sfc, pc, p, cc, cbh)[0]

    fuel = spec.code
    if fuel in (FuelType.M1, FuelType.M2):
        c2, d1 = FUEL_TYPES[FuelType.C2], FUEL_TYPES[FuelType.D1]
        isf = pc / 100.0 * _isf_from_rsf(c2, rsz(c2, pdf) * sf) + (1.0 - pc / 100.0) * _isf_from_rsf(
            d1, rsz(d1, pdf) * sf
        )
    elif fuel in (FuelType.M3, FuelType.M4):
        d1 = FUEL_TYPES[FuelType.D1]
        isf = pdf / 100.0 * _isf_from_rsf(spec, rsz(spec, 100.0) * sf) + (
            1.0 - pdf / 100.0
        ) * _isf_from_rsf(d1, rsz(d1, 100.0) * sf)
    elif fuel in _GRASS:
        isf = _isf_from_rsf(spec, rsz(spec, pdf) * sf, calculate_grass_curing_factor(cc))
    else:
        isf = _isf_from_rsf(spec, rsz(spec, pdf) * sf)

    if not isf > 0.0:
        return wind_speed, wind_to_deg % 360.0

    ff = _fine_fuel_moisture_function(ffmc)
    wse = 1.0 / 0.05039 * math.log(isf / (0.208 * ff))
    if wse > 40.0:
        if isf < 0.999 * 2.496 * ff:
            wse = 28.0 - (1.0 / 0.0818 * math.log(1.0 - isf / (2.496 * ff)))
        else:
            wse = 112.45

    waz = math.radians(wind_to_deg)
    saz = math.radians(upslope_deg)
    wsx = wind_speed * math.sin(waz) + wse * math.sin(saz)
    wsy = wind_speed * math.cos(waz) + wse * math.cos(saz)
    wsv = math.hypot(wsx, wsy)
    raz = math.degrees(math.atan2(wsx, wsy)) % 360.0 if wsv > 0.0 else wind_to_deg % 360.0
    return wsv, raz


def _calculate_flame_length(hfi: float) -> float:
    """Calculate flame length from head fire intensity.

    Byram (1959): L = 0.0775 * I^0.46

    Args:
        hfi: Head fire intensity (kW/m)

    Returns:
        Flame length in meters
    """
    if hfi <= 0.0:
        return 0.0
    return 0.0775 * hfi**0.46


def calculate_flame_length(hfi: float) -> float:
    """Public wrapper for Byram (1959) flame length (m) from HFI (kW/m)."""
    return _calculate_flame_length(hfi)


def calculate_fbp(
    fuel_type: FuelType | str,
    wind_speed: float,
    ffmc: float,
    dmc: float,
    dc: float,
    slope: float = 0.0,
    pc: float = 50.0,
    grass_cure: float = 60.0,
    fmc: float = 100.0,
    *,
    wind_direction: float = 0.0,
    slope_aspect: float | None = None,
    pdf: float = 35.0,
    gfl: float = 0.35,
    cbh: float | None = None,
    cfl: float | None = None,
) -> FBPResult:
    """Calculate complete fire behavior prediction for the head, flank and back.

    This is the main entry point for FBP calculations.

    Args:
        fuel_type: FBP fuel type code (e.g., FuelType.C2 or "C2")
        wind_speed: 10-m open wind speed (km/h)
        ffmc: Fine Fuel Moisture Code (0-101)
        dmc: Duff Moisture Code
        dc: Drought Code
        slope: Ground slope (%)
        pc: Percent conifer (0-100, for M1/M2 types)
        grass_cure: Percent curing (0-100, for O1a/O1b types)
        fmc: Foliar moisture content (%, for crown fire calculation)
        wind_direction: Meteorological wind direction (degrees FROM)
        slope_aspect: Upslope azimuth (degrees, 0=N). None = upslope
            aligned with the wind (the worst case).
        pdf: Percent dead balsam fir (M3/M4)
        gfl: Grass fuel load (kg/m2, O1a/O1b)
        cbh: Crown base height override (m); default from the fuel type
        cfl: Crown fuel load override (kg/m2); default from the fuel type

    Returns:
        FBPResult with all fire behavior outputs
    """
    if isinstance(fuel_type, str):
        fuel_type = FuelType(fuel_type)
    spec = get_fuel_spec(fuel_type)

    bui = calculate_bui(dmc, dc)
    cbh = spec.cbh if cbh is None else cbh
    cfl = spec.cfl if cfl is None or cfl <= 0.0 else cfl
    if fuel_type in _NO_CROWN_FMC:
        fmc = 0.0
    sfc = calculate_sfc(spec, ffmc, bui, pc, gfl)

    # Net effective wind (slope folded in as an equivalent wind, ST-X-3 eqs 39-50)
    wind_to = (wind_direction + 180.0) % 360.0
    if slope > 0.0 and ffmc > 0.0:
        upslope = wind_to if slope_aspect is None else slope_aspect
        wsv, raz = calculate_slope_adjustment(
            spec, ffmc, wind_speed, wind_to, slope, upslope, fmc, sfc, pc, pdf, grass_cure, cbh
        )
    else:
        wsv, raz = wind_speed, wind_to

    isi = calculate_isi(ffmc, wsv)
    ros, rss, cfb, csi, rso = _rate_of_spread(spec, isi, bui, fmc, sfc, pc, pdf, grass_cure, cbh)
    if cfl <= 0.0:
        cfb = 0.0

    # Crown fuel consumption (ST-X-3 eq 66; M types scaled by their crown share)
    cfl_available = cfl
    if fuel_type in (FuelType.M1, FuelType.M2):
        cfl_available *= pc / 100.0
    elif fuel_type in (FuelType.M3, FuelType.M4):
        cfl_available *= pdf / 100.0
    cfc = cfl_available * cfb
    tfc = sfc + cfc

    sfi = _INTENSITY_FACTOR * sfc * rss
    hfi = _INTENSITY_FACTOR * tfc * ros

    # Ellipse: back rate from the back ISI, flank from head + back (ST-X-3 eqs 75-89)
    lb = calculate_length_to_breadth(fuel_type, wsv)
    bisi = calculate_back_isi(ffmc, wsv)
    bros = _rate_of_spread(spec, bisi, bui, fmc, sfc, pc, pdf, grass_cure, cbh)[0]
    fros = (ros + bros) / lb / 2.0

    return FBPResult(
        fuel_type=fuel_type.value,
        isi=isi,
        bui=bui,
        ros_surface=rss,
        ros_final=ros,
        sfc=sfc,
        cfc=cfc,
        tfc=tfc,
        sfi=sfi,
        hfi=hfi,
        cfb=cfb,
        fire_type=classify_fire_type(cfb),
        flame_length=_calculate_flame_length(hfi),
        back_ros=bros,
        flank_ros=fros,
        lb=lb,
        wsv=wsv,
        raz=raz,
        csi=csi,
        rso=rso,
        fmc=fmc,
        cfl=cfl_available,
    )
