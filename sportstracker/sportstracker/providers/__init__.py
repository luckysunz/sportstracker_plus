"""Provider registry."""

from __future__ import annotations

from datetime import tzinfo
from typing import Any, Dict

from ..errors import ProviderError
from .base import Provider


def create_provider(name: str, params: Dict[str, Any], http: Any, tz: tzinfo) -> Provider:
    """Create the provider called ``name`` with the given provider_params."""
    if name == "openligadb":
        from .openligadb import OpenLigaDbProvider

        return OpenLigaDbProvider.from_params(params, http, tz)
    if name == "genius_rbbl":
        from .genius_rbbl import GeniusRbblProvider

        return GeniusRbblProvider.from_params(params, http, tz)
    raise ProviderError("unknown provider: %r" % name)
