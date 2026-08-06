"""Pydantic models mirroring db/schema.sql.

Field names/shapes follow the raw Planetarion-style dump format served at
https://game.planetarion.com/botfiles/ -- see btk/dumps/parser.py for the
exact file layouts these are parsed from.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Round(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: int
    name: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class Tick(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    round_id: int
    number: int
    etag: str | None = None
    last_modified: str | None = None
    inserted_at: datetime


class Alliance(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    round_id: int
    name: str


class AllianceStat(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tick_id: int
    alliance_id: int
    rank: int
    size: int
    members: int
    score: int
    points: int
    total_score: int
    total_value: int


class Galaxy(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    round_id: int
    x: int
    y: int


class GalaxyStat(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tick_id: int
    galaxy_id: int
    name: str
    size: int
    score: int
    value: int
    xp: int


class Planet(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    round_id: int
    external_id: str


class PlanetStat(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tick_id: int
    planet_id: int
    galaxy_id: int | None = None
    x: int
    y: int
    z: int
    planet_name: str
    ruler_name: str
    race: str
    size: int
    score: int
    value: int
    xp: int
    special: str


class Feed(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    round_id: int
    tick_number: int
    category: str
    text: str


class Ship(BaseModel):
    # `class` is a reserved word in Python, but is the actual column name in
    # db/schema.sql and the actual key in stats.json -- alias bridges both.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    round_id: int
    name: str
    class_: str = Field(alias="class")
    race: str
    type: str
    metal: int
    crystal: int
    eonium: int
    total_cost: int
    damage: int
    armor: int
    guns: int
    initiative: int
    empres: int
    baseeta: int
    target1: str | None = None
    target2: str | None = None
    target3: str | None = None
