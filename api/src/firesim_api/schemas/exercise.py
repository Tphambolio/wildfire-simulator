"""Exercise mode scenario packs (docs/exercise-mode-spec.md §5, §11; PR2).

``ScenarioPack`` mirrors ``frontend/src/types/exercise.ts`` (field names are the file format's
camelCase). Keep this module, ``docs/api-reference.md`` ("Exercise packs") and the TS types in
step.

Framing rules enforced here (owner's hard rules, spec §10):

* Every model forbids unknown fields (``extra="forbid"``), so a pack cannot carry an
  evacuation tier, an address or any other field the format does not define.
* ``marking`` is always ``"EXERCISE"``.
* Inject kinds are closed (perimeter, spot_fire, wind_shift, resource, note); a payload's
  ``kind`` must equal its inject's.
* Text is length-limited plain text (control and direction characters rejected).

Owner defaults (2026-10-30, Q12): ``durationHours`` ≤ 72, ``forecastHorizonHours`` 36 (pack-
settable, ≤ 72), ``autoRun`` false. The 2 MB size limit is checked by the router on the raw body.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PACK_SCHEMA = "firesim.exercise.pack/1"
MAX_DURATION_HOURS = 72.0
DEFAULT_FORECAST_HORIZON_HOURS = 36.0
#: Largest pack the API stores (bytes of the JSON body), owner default 2026-10-30
MAX_PACK_BYTES = 2 * 1024 * 1024

FBP_FUEL_TYPES = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "D1", "D2", "M1", "M2", "M3", "M4",
                  "O1a", "O1b", "S1", "S2", "S3")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
_PACK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _plain(v: str) -> str:
    if _CONTROL.search(v):
        raise ValueError("control or direction characters are not allowed")
    return v.strip()


def _offset_time(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("needs a UTC offset (e.g. -06:00)")
    return v


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Text80 = Annotated[str, Field(min_length=1, max_length=80)]
Text300 = Annotated[str, Field(min_length=1, max_length=300)]


class WeatherScriptStep(_Strict):
    """One step of a weather script: this weather from ``hoursFromStart`` until the next step."""

    hoursFromStart: float = Field(ge=0, le=MAX_DURATION_HOURS)
    windSpeedKmh: float = Field(ge=0, le=100)
    windDirectionDeg: float = Field(ge=0, lt=360, description="Direction the wind blows FROM")
    temperatureC: float = Field(ge=-40, le=50)
    relativeHumidityPct: float = Field(ge=1, le=100)
    precipitation24hMm: float = Field(
        0.0, ge=0, le=200,
        description="First step: 24 h rain before the start (Setup's weather, an FWI input). "
                    "Later steps: ignored (use precipitationMmH).")
    precipitationMmH: float = Field(0.0, ge=0, le=50, description="Rain rate during a later step")


class ScenarioPackIgnition(_Strict):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    hoursFromStart: float = Field(ge=0, le=MAX_DURATION_HOURS)


class ScenarioPackPeriod(_Strict):
    number: int = Field(ge=1, le=24)
    startIso: datetime
    endIso: datetime

    _offsets = field_validator("startIso", "endIso")(_offset_time)


class StartingCodes(_Strict):
    ffmc: float | None = Field(None, ge=0, le=101)
    dmc: float | None = Field(None, ge=0, le=1000)
    dc: float | None = Field(None, ge=0, le=2000)


class PackFuel(_Strict):
    source: Literal["city_lidar", "ab_wildfire", "uniform"]
    uniformFuelType: str | None = None

    @model_validator(mode="after")
    def _uniform_type(self) -> PackFuel:
        if self.source == "uniform":
            if self.uniformFuelType not in FBP_FUEL_TYPES:
                raise ValueError(f"uniformFuelType must be one of {', '.join(FBP_FUEL_TYPES)}")
        elif self.uniformFuelType is not None:
            raise ValueError("uniformFuelType is only for source 'uniform'")
        return self


class PlaceAlias(_Strict):
    """A community shown under another name in this exercise (e.g. a fictional name on real
    geography; owner decision 2026-10-30)."""

    place: Text80 = Field(description="The community's name in the app (places.geojson `place`)")
    shownAs: Text80

    _p = field_validator("place", "shownAs")(_plain)


# ── Inject payloads (closed set; no tier, no address fields) ─────────────────


class PerimeterPayload(_Strict):
    kind: Literal["perimeter"]
    perimeterSource: Literal["truth_run", "drawn"] = Field(
        description="truth_run: the truth run's own perimeter at the inject time (PR3 delivers "
                    "it); drawn: perimeterGeojson")
    perimeterGeojson: dict | None = Field(None, description="Polygon / MultiPolygon geometry, [lng, lat]")
    activeEdges: dict | None = None

    @model_validator(mode="after")
    def _geometry(self) -> PerimeterPayload:
        if self.perimeterSource == "drawn":
            g = self.perimeterGeojson
            if not isinstance(g, dict) or g.get("type") not in ("Polygon", "MultiPolygon") \
                    or not isinstance(g.get("coordinates"), list):
                raise ValueError("a drawn perimeter needs a Polygon or MultiPolygon geometry")
        return self


class SpotFirePayload(_Strict):
    kind: Literal["spot_fire"]
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class WindShiftPayload(_Strict):
    kind: Literal["wind_shift"]
    windSpeedKmh: float = Field(ge=0, le=100)
    windDirectionDeg: float = Field(ge=0, lt=360)
    hiddenFromForecast: bool = True


class ResourcePayload(_Strict):
    kind: Literal["resource"]
    text: str = Field(min_length=1, max_length=500)

    _t = field_validator("text")(_plain)


class NotePayload(_Strict):
    kind: Literal["note"]
    text: str = Field(min_length=1, max_length=500)

    _t = field_validator("text")(_plain)


InjectPayload = Annotated[
    Union[PerimeterPayload, SpotFirePayload, WindShiftPayload, ResourcePayload, NotePayload],
    Field(discriminator="kind"),
]


class ScenarioPackInject(_Strict):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["perimeter", "spot_fire", "wind_shift", "resource", "note"]
    hoursFromStart: float = Field(ge=0, le=MAX_DURATION_HOURS)
    title: Text80
    payload: InjectPayload

    _t = field_validator("title")(_plain)

    @model_validator(mode="after")
    def _kind_matches(self) -> ScenarioPackInject:
        if self.payload.kind != self.kind:
            raise ValueError("payload.kind must equal the inject's kind")
        return self


# ── Pack ──────────────────────────────────────────────────────────────────────


class ScenarioPack(_Strict):
    schema_: Literal["firesim.exercise.pack/1"] = Field(alias="schema")
    id: str = Field(description="Server-assigned 32-hex ID once stored; any [A-Za-z0-9_-]{1,64} in a file")
    marking: Literal["EXERCISE"] = "EXERCISE"
    title: Text80
    summary: str | None = Field(None, max_length=500)
    sources: list[Text300] = Field(min_length=1, max_length=30)
    caveats: list[Text300] = Field(default_factory=list, max_length=20)
    startIso: datetime
    durationHours: float = Field(gt=0, le=MAX_DURATION_HOURS)
    forecastHorizonHours: float = Field(DEFAULT_FORECAST_HORIZON_HOURS, gt=0, le=MAX_DURATION_HOURS)
    autoRun: bool = False
    ignitions: list[ScenarioPackIgnition] = Field(min_length=1, max_length=20)
    weatherScript: list[WeatherScriptStep] = Field(min_length=1, max_length=500)
    playerForecast: list[WeatherScriptStep] | None = Field(None, max_length=500)
    startingCodes: StartingCodes
    grassCuringPct: float | None = Field(None, ge=0, le=100)
    fuel: PackFuel
    periods: list[ScenarioPackPeriod] = Field(min_length=1, max_length=24)
    scheduledInjects: list[ScenarioPackInject] = Field(default_factory=list, max_length=200)
    placeAliases: list[PlaceAlias] = Field(default_factory=list, max_length=20)
    prePlacedAssets: dict | None = None
    notesForFacilitator: str | None = Field(None, max_length=5000)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    _start = field_validator("startIso")(_offset_time)
    _text = field_validator("title")(_plain)

    @field_validator("sources", "caveats")
    @classmethod
    def _plain_list(cls, v: list[str]) -> list[str]:
        return [_plain(x) for x in v]

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _PACK_ID.match(v):
            raise ValueError("id must be 1-64 characters of A-Z a-z 0-9 _ -")
        return v

    @field_validator("prePlacedAssets")
    @classmethod
    def _assets(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if v.get("type") != "FeatureCollection" or not isinstance(v.get("features"), list):
            raise ValueError("prePlacedAssets must be a GeoJSON FeatureCollection")
        if len(v["features"]) > 500:
            raise ValueError("at most 500 pre-placed assets")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> ScenarioPack:
        d = self.durationHours
        if not any(i.hoursFromStart == 0 for i in self.ignitions):
            raise ValueError("at least one ignition must be at hoursFromStart 0")
        if any(i.hoursFromStart >= d for i in self.ignitions):
            raise ValueError("ignitions must be before the end of the exercise")
        for name, steps in (("weatherScript", self.weatherScript), ("playerForecast", self.playerForecast)):
            if not steps:
                continue
            hours = [s.hoursFromStart for s in steps]
            if hours[0] != 0:
                raise ValueError(f"{name} must start with a step at hoursFromStart 0")
            if any(b <= a for a, b in zip(hours, hours[1:])):
                raise ValueError(f"{name} steps must be in strictly increasing hoursFromStart")
            if hours[-1] >= d:
                raise ValueError(f"{name} steps must start before the end of the exercise")
        start = self.startIso
        end = start + timedelta(hours=d)
        nums = [p.number for p in self.periods]
        if nums != list(range(1, len(nums) + 1)):
            raise ValueError("periods must be numbered 1, 2, 3 ... in order")
        prev_end = start
        for p in self.periods:
            if p.endIso <= p.startIso:
                raise ValueError(f"period {p.number} ends at or before its start")
            if p.startIso < prev_end:
                raise ValueError(f"period {p.number} starts before the exercise or overlaps the previous one")
            if p.endIso > end:
                raise ValueError(f"period {p.number} ends after the exercise")
            prev_end = p.endIso
        ids = [j.id for j in self.scheduledInjects]
        if len(set(ids)) != len(ids):
            raise ValueError("scheduledInjects ids must be unique")
        if any(j.hoursFromStart > d for j in self.scheduledInjects):
            raise ValueError("scheduled injects must be within the exercise")
        return self

    def to_json_dict(self) -> dict:
        """The pack as its file format (``schema`` key, ISO times as given)."""
        return self.model_dump(mode="json", by_alias=True)


class PackSummary(BaseModel):
    id: str
    title: str
    startIso: str | None
    durationHours: float | None
    sources: list[str]
    createdAt: str


class PackList(BaseModel):
    packs: list[PackSummary]
    total: int


class ExamplePackList(BaseModel):
    packs: list[dict] = Field(description="Full example packs (public, from the repository)")
