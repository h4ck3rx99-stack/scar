"""Contact resolution: ambiguity is surfaced, aliases pick the right person, pronouns need an explicit referent."""

from __future__ import annotations

import pytest

from scar.core.errors import CapabilityUnavailable
from scar.core.types import ToolStatus
from scar.integrations.email_types import EmailAddress
from scar.tools.contacts.resolver import ContactResolver

MODULES = ["scar.tools.internal", "scar.tools.contacts.tools"]


class FakeEmail:
    """Stands in for EmailService's contact helpers."""

    def __init__(self, recent: list[EmailAddress] | None = None, book: list[dict] | None = None, fail: bool = False) -> None:
        self.recent = recent or []
        self.book = book or []
        self.fail = fail

    async def search_contacts(self, query: str, limit: int = 10) -> list[dict]:
        if self.fail:
            raise CapabilityUnavailable("EMAIL not configured", "docs/integrations/gmail.md")
        return [c for c in self.book if query.casefold() in c["name"].casefold()]

    async def recent_correspondents(self, limit: int = 50) -> list[EmailAddress]:
        if self.fail:
            raise CapabilityUnavailable("EMAIL not configured", "docs/integrations/gmail.md")
        return self.recent


@pytest.fixture
def resolver(services) -> ContactResolver:
    r = ContactResolver(services.db)
    r.add("John Smith", ["john.smith@uni.example"])
    r.add("John Doe", ["jdoe@lab.example"], handles={"telegram": "@jdoe"})
    r.add("Maria Garcia", ["maria@example.com"], phones=["+34 600 111 222"])
    return r


async def test_two_johns_are_ambiguous_never_guessed(resolver: ContactResolver) -> None:
    res = await resolver.resolve("John")
    assert res.status == "ambiguous" and res.best is None
    assert {c.name for c in res.candidates} == {"John Smith", "John Doe"}
    assert "Which John" in res.question and "john.smith@uni.example" in res.question


async def test_full_name_email_phone_and_handle_resolve(resolver: ContactResolver) -> None:
    assert (await resolver.resolve("John Smith")).best.emails == ["john.smith@uni.example"]
    assert (await resolver.resolve("JDOE@lab.example")).best.name == "John Doe"
    assert (await resolver.resolve("600 111 222")).best.name == "Maria Garcia"
    assert (await resolver.resolve("@jdoe", channel="telegram")).best.name == "John Doe"
    maria = await resolver.resolve("maria")
    assert maria.status == "resolved" and maria.best.name == "Maria Garcia"  # single first-name match


async def test_alias_picks_the_right_person(resolver: ContactResolver) -> None:
    doe = (await resolver.resolve("John Doe")).best
    resolver.set_alias("my professor", doe.contact_id)
    res = await resolver.resolve("my professor")
    assert res.status == "resolved" and res.best.name == "John Doe" and res.best.confidence == 1.0
    assert (await resolver.resolve("My Professor")).best.contact_id == doe.contact_id


async def test_pronouns_need_explicit_unambiguous_referent(resolver: ContactResolver) -> None:
    res = await resolver.resolve("him")
    assert res.status == "not_found" and "him" in res.question
    maria = (await resolver.resolve("Maria Garcia")).best
    resolver.set_last_discussed(maria.contact_id)
    assert (await resolver.resolve("her")).best.name == "Maria Garcia"
    both = [c.contact_id for c in resolver.all_contacts() if c.name.startswith("John")]
    resolver.set_last_discussed(both)
    res = await resolver.resolve("him")
    assert res.status == "ambiguous" and len(res.candidates) == 2


async def test_remote_sources_are_discounted_and_single_weak_match_asks(services) -> None:
    email = FakeEmail(
        recent=[EmailAddress("priya.patel@corp.example", "Priya Patel")],
        book=[{"name": "Omar Haddad", "emails": ["omar@corp.example"], "phones": [], "source": "google"}],
    )
    r = ContactResolver(services.db, email=email)
    res = await r.resolve("Priya")
    assert res.status == "ambiguous" and res.question.startswith("Do you mean Priya Patel")
    exact = await r.resolve("Omar Haddad")
    assert exact.status == "resolved" and exact.best.emails == ["omar@corp.example"] and exact.best.source == "google"
    assert (await r.resolve("priya.patel@corp.example")).best.source == "recent_email"


async def test_unconfigured_email_is_noted_not_fatal(services) -> None:
    r = ContactResolver(services.db, email=FakeEmail(fail=True))
    r.add("Lee Chen", ["lee@example.com"])
    res = await r.resolve("Lee")
    assert res.status == "resolved" and any("unavailable" in n for n in res.notes)


@pytest.fixture
async def pipe(services):
    from scar.runtime.registration import build_registry
    from scar.tools.pipeline import ToolPipeline

    return ToolPipeline(services, build_registry(services, modules=MODULES))


async def test_resolve_tool_surfaces_ambiguity_for_ask_user(pipe, services, ctx_factory) -> None:
    ctx = ctx_factory("email John about the meeting")
    for name, addr in (("John Smith", "john.smith@uni.example"), ("John Doe", "jdoe@lab.example")):
        obs = await pipe.execute("contacts.add", {"name": name, "emails": [addr]}, ctx)
        assert obs.result.ok and obs.result.verification.verified is True
    obs = await pipe.execute("contacts.resolve", {"query": "John", "include_remote": False}, ctx)
    assert obs.result.ok and obs.result.data["status"] == "ambiguous"
    assert "AMBIGUOUS" in obs.result.model_view and "Do not choose yourself" in obs.result.model_view
    assert obs.result.data["ask_user"]["options"] == ["John Doe <jdoe@lab.example>", "John Smith <john.smith@uni.example>"]
    # an alias to an ambiguous name is refused
    obs = await pipe.execute("contacts.alias", {"alias": "my professor", "contact": "John"}, ctx)
    assert obs.result.status == ToolStatus.ERROR and obs.result.error_type == "Ambiguous"
    obs = await pipe.execute("contacts.alias", {"alias": "my professor", "contact": "John Doe"}, ctx)
    assert obs.result.ok and obs.result.verification.verified is True
    obs = await pipe.execute("contacts.resolve", {"query": "my professor", "include_remote": False}, ctx)
    assert obs.result.data["status"] == "resolved"
    assert obs.result.data["candidates"][0]["emails"] == ["jdoe@lab.example"]
