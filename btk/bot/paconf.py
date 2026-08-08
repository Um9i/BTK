"""Static Planetarion game constants, ported from Merlin's pa.cfg.

These are fixed game-rule numbers (not per-round config) -- they don't
change round to round, so they're hardcoded here rather than modeled as
settings.
"""

SHIP_VALUE = 100
ROID_VALUE = 200
RES_VALUE = 150
CONS_VALUE = 200
XP_VALUE = 60

TICK_LENGTH = 3600  # seconds
ROID_MINING = 250

TARGET_EFFICIENCY = {"t1": 1.0, "t2": 0.7, "t3": 0.5}

# name, prodcost bonus (resource cost multiplier), prodtime bonus (build
# time multiplier). Matches pa.cfg's [cor]/[dem]/[nat]/[soc]/[tot] sections
# -- note pa.cfg's [govs] enum section itself excludes "ana" (Anarchy), so
# it's left out here too.
GOVS: dict[str, dict[str, float | str]] = {
    "cor": {"name": "Corp", "prodcost": 0.00, "prodtime": 0.00},
    "dem": {"name": "Demo", "prodcost": -0.08, "prodtime": 0.00},
    "nat": {"name": "Nation", "prodcost": 0.00, "prodtime": -0.10},
    "soc": {"name": "Social", "prodcost": 0.00, "prodtime": 0.20},
    "tot": {"name": "Total", "prodcost": -0.08, "prodtime": -0.10},
}

# race prodtime bonus, keyed by the first 3 letters of the race name
# lowercased (matches Ship.race[:3].lower(), with Merlin's "eit" -> "etd"
# quirk since Eitraides used the Etdolysian production-time bonus).
RACE_PRODTIME = {
    "ter": 0.10,
    "cat": 0.00,
    "xan": 0.10,
    "zik": 0.20,
    "etd": 0.00,
    "eit": 0.00,
}

CLASS_ETA = {"fi": 8, "co": 8, "fr": 9, "de": 9, "cr": 10, "bs": 10}

# Requestable scan types (pa.cfg's [P]/[D]/[U]/[N]/[J]/[A] sections --
# "type" is the waves.pl scan-type id). Landing (L, type 2) and Incoming
# (I, type 6) are excluded: pa.cfg marks both request=False, since they're
# automatic rather than player-requestable.
SCAN_TYPES = {
    "P": {"name": "Planet", "type": 1},
    "D": {"name": "Development", "type": 3},
    "U": {"name": "Unit", "type": 4},
    "N": {"name": "News", "type": 5},
    "J": {"name": "Jumpgate Probe", "type": 7},
    "A": {"name": "Advanced Unit", "type": 8},
}
