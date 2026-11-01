"""League presets offered by the web backend.

Each preset fixes the provider and its ``provider_params``. Anything not
listed here can still be configured by editing ``config/config.json`` by
hand; the web form then shows it as "Benutzerdefiniert" and leaves it alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class League:
    league_id: str
    label: str
    provider: str
    params: Dict[str, Any] = field(default_factory=dict)


LEAGUES: List[League] = [
    League("rbbl1", "RBBL 1 – Basketball, Saison 2026/27 (Genius Sports)", "genius_rbbl", {"competition_id": 49807}),
    League("rbbl2a", "RBBL 2 Div. A – Basketball, Saison 2026/27 (Genius Sports)", "genius_rbbl", {"competition_id": 49808}),
    League("del", "DEL – Eishockey, Saison 2026 (OpenLigaDB)", "openligadb",
           {"league_shortcut": "del", "season": 2026, "league_id": 4947}),
]

CUSTOM_ID = "custom"


def get_league(league_id: str) -> Optional[League]:
    for league in LEAGUES:
        if league.league_id == league_id:
            return league
    return None


def find_league(provider: str, params: Dict[str, Any]) -> Optional[League]:
    """The preset whose provider and parameters are all contained in the given configuration."""
    for league in LEAGUES:
        if league.provider == provider and all(str(params.get(k)) == str(v) for k, v in league.params.items()):
            return league
    return None
