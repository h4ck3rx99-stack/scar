"""Google People API: search the user's contacts (``people:searchContacts``)."""

from __future__ import annotations

from typing import Any

import httpx

from scar.integrations.common import ApiClient, ServiceInfo, TokenSource
from scar.integrations.google.auth import LOGIN_COMMAND, SETUP_DOC

PEOPLE_BASE = "https://people.googleapis.com/v1"
READ_MASK = "names,emailAddresses,phoneNumbers"


def person_to_contact(raw: dict[str, Any]) -> dict[str, Any]:
    names = raw.get("names") or []
    name = str(names[0].get("displayName") or "") if names else ""
    emails = [str(e["value"]) for e in raw.get("emailAddresses") or [] if e.get("value")]
    phones = [str(p["value"]) for p in raw.get("phoneNumbers") or [] if p.get("value")]
    return {
        "name": name or (emails[0] if emails else ""),
        "emails": emails,
        "phones": phones,
        "source": "google",
        "remote_id": str(raw.get("resourceName") or ""),
    }


class GooglePeopleClient:
    def __init__(self, tokens: TokenSource, http: httpx.AsyncClient | None = None) -> None:
        self.api = ApiClient(
            ServiceInfo("Google People", SETUP_DOC, f"run `{LOGIN_COMMAND}`"), tokens=tokens, http=http, base_url=PEOPLE_BASE
        )
        self._warmed = False

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if not self._warmed:
            # Google requires a warm-up request with an empty query to refresh the search cache.
            await self.api.get_json("people:searchContacts", params={"query": "", "readMask": READ_MASK})
            self._warmed = True
        body = (
            await self.api.get_json(
                "people:searchContacts", params={"query": query, "readMask": READ_MASK, "pageSize": min(limit, 30)}
            )
            or {}
        )
        return [person_to_contact(r.get("person") or {}) for r in body.get("results") or []]
