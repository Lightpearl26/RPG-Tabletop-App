# -*- coding: utf-8 -*-

"""Names of the character statistics, shared by every layer."""

# The ten core statistics, in display order
CORE_STATS = ("str", "con", "dex", "agi", "int", "wis", "cha", "luc", "sur", "per")

# Gauges with a current value and a maximum (`<stat>_max`)
RESOURCE_STATS = ("hp", "stamina", "mental_health", "drug_health")

# Gauges that only the GM may ever see or test
PRIVATE_STATS = frozenset({"mental_health", "drug_health"})

# Gauges a player may see
PUBLIC_RESOURCE_STATS = tuple(stat for stat in RESOURCE_STATS if stat not in PRIVATE_STATS)
