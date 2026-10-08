"""
"Mein lokales Modell antwortet nicht - soll ich die Cloud fragen?"

When a turn fails because the LOCAL model is unavailable, Cypher may offer
to answer that one request via the cloud - never automatically, because
the conversation (and memory) would leave the PC. The offer is shown like
an approval card (approval_id "cloud-...") and is answered the same way:
button, J/N key, or a spoken "ja"/"nein". It expires after OFFER_TTL_SECONDS.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from app.config import Settings
from app.core.errors import CypherError
from app.llm.factory import cloud_available

OFFER_TTL_SECONDS = 120
PREFIX = "cloud-"
# Local failures where asking the cloud makes sense.
_OFFERABLE = {"ollama_unreachable", "model_missing", "llm_timeout", "llm_bad_response"}

SPOKEN_QUESTION = "Mein lokales Modell antwortet gerade nicht. Soll ich es über die Cloud versuchen?"


@dataclass
class CloudOffer:
    offer_id: str
    conversation_id: str
    text: str
    source: str
    created_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created_at > OFFER_TTL_SECONDS


_lock = threading.Lock()
_offers: Dict[str, CloudOffer] = {}


def should_offer(error: CypherError, provider_name: str, settings: Settings) -> bool:
    return provider_name == "local" and error.code in _OFFERABLE and settings.CLOUD_FALLBACK_OFFER and cloud_available(settings)


def create(conversation_id: str, text: str, source: str) -> CloudOffer:
    offer = CloudOffer(PREFIX + uuid.uuid4().hex[:16], conversation_id, text, source)
    with _lock:
        for key in [k for k, o in _offers.items() if o.expired]:
            _offers.pop(key, None)
        _offers[offer.offer_id] = offer
    return offer


def pop(offer_id: str) -> Optional[CloudOffer]:
    """Single use; None if unknown or expired."""
    with _lock:
        offer = _offers.pop(offer_id, None)
    return None if offer is None or offer.expired else offer


def is_offer_id(approval_id: str) -> bool:
    return approval_id.startswith(PREFIX)


def card(offer: CloudOffer, model: str) -> Dict[str, object]:
    return {
        "approval_id": offer.offer_id,
        "tool_name": "cloud_fallback",
        "arguments": {},
        "summary": f"Diese Anfrage an die Cloud ({model}) schicken - Gespräch und Gedächtnis verlassen dafür den PC.",
        "risk": "cloud",
        "expires_in_seconds": OFFER_TTL_SECONDS,
    }


def clear() -> None:
    with _lock:
        _offers.clear()
