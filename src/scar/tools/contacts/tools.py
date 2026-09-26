"""Contact tools: resolve people (never guessing), add local contacts, define aliases."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from scar.core.errors import ToolError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, VerificationResult
from scar.security.risk import RiskAssessment
from scar.tools.base import Tool, ToolContext, ToolInput
from scar.tools.contacts.resolver import get_resolver, normalise_alias

Channel = Literal["email", "telegram", "telegram_user", "discord", "whatsapp"]


class ResolveInput(ToolInput):
    query: str = Field(description="Name, alias ('my professor'), email/phone/handle, or a pronoun ('him')")
    channel: Channel | None = Field(None, description="Prefer people reachable on this channel")
    include_remote: bool = Field(True, description="Also search the email provider's address book and recent mail")


class ContactsResolve(Tool):
    name = "contacts.resolve"
    description = (
        "Find who a person reference means. Returns status resolved / ambiguous / not_found with scored "
        "candidates. If ambiguous or not_found, call ask_user with the returned question and the "
        "candidate labels as options - never pick one yourself."
    )
    input_model = ResolveInput
    capabilities = ("contacts.read",)
    data_class = "contacts"
    categories = ("contacts", "email", "messaging", "calendar")

    def describe(self, args: ResolveInput) -> str:
        return f"look up contact {args.query!r}"

    async def run(self, args: ResolveInput, ctx: ToolContext) -> ToolResult:
        resolver = get_resolver(ctx.services)
        res = await resolver.resolve(args.query, include_remote=args.include_remote, channel=args.channel)
        data = res.to_dict()
        if res.status == "resolved" and res.best is not None:
            best = res.best
            resolver.set_last_discussed(best.contact_id or (best.emails[0] if best.emails else best.name))
            view = f"Resolved {args.query!r} -> {best.label()} (confidence {best.confidence:.2f}: {best.reason})"
            return self.ok(view, data, model_view=view)
        labels = [c.label() for c in res.candidates]
        data["ask_user"] = {"question": res.question, "options": labels}
        if res.status == "ambiguous":
            view = (
                f"AMBIGUOUS: {len(labels)} people match {args.query!r}: "
                + "; ".join(labels)
                + f". Ask the user (ask_user) - question: {res.question!r}. Do not choose yourself."
            )
        else:
            view = f"NOT FOUND: no contact matches {args.query!r}. Ask the user: {res.question!r}"
        if res.notes:
            view += "\nNotes: " + "; ".join(res.notes)
        return self.ok(f"{res.status}: {args.query}", data, model_view=view)


class AddInput(ToolInput):
    name: str = Field(min_length=1, max_length=200)
    emails: list[str] = Field(default_factory=list, max_length=20)
    phones: list[str] = Field(default_factory=list, max_length=20)
    handles: dict[str, str] = Field(default_factory=dict, description="e.g. {'telegram': '@ann', 'discord': '1234'}")


class ContactsAdd(Tool):
    name = "contacts.add"
    description = "Save a person to SCAR's local contacts (merges into an existing contact with the same email)."
    input_model = AddInput
    capabilities = ("contacts.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("contacts",)

    def assess(self, args: AddInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        a.facts.data_classes.append("contacts")
        return a

    def describe(self, args: AddInput) -> str:
        ident = ", ".join([*args.emails, *args.phones, *args.handles.values()]) or "no details"
        return f"save contact {args.name} ({ident})"

    async def run(self, args: AddInput, ctx: ToolContext) -> ToolResult:
        resolver = get_resolver(ctx.services)
        contact_id = resolver.add(args.name, args.emails, args.phones, args.handles)
        return self.ok(f"Saved contact {args.name}", {"contact_id": contact_id, "name": args.name})

    async def verify(self, args: AddInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        c = get_resolver(ctx.services).get(result.data["contact_id"])
        checks = [Check(name="contact stored", passed=c is not None)]
        if c is not None:
            stored = {e.casefold() for e in c.emails}
            checks.append(Check(name="emails stored", passed=all(e.strip().casefold() in stored for e in args.emails)))
        return VerificationResult.from_checks(checks, {"contact_id": result.data["contact_id"]})


class AliasInput(ToolInput):
    alias: str = Field(min_length=1, max_length=100, description="What the user calls them, e.g. 'my professor'")
    contact: str = Field(description="contact_id (ct_...) or an unambiguous name/email to resolve")


class ContactsAlias(Tool):
    name = "contacts.alias"
    description = (
        "Remember that an alias (e.g. 'my professor', 'mom') means a specific contact. The contact "
        "must resolve unambiguously; otherwise ask the user first."
    )
    input_model = AliasInput
    capabilities = ("contacts.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("contacts",)

    def describe(self, args: AliasInput) -> str:
        return f"remember {args.alias!r} means {args.contact}"

    async def run(self, args: AliasInput, ctx: ToolContext) -> ToolResult:
        resolver = get_resolver(ctx.services)
        target = resolver.get(args.contact) if args.contact.startswith("ct_") else None
        if target is None:
            res = await resolver.resolve(args.contact, include_remote=False)
            if res.status != "resolved" or res.best is None:
                raise ToolError(
                    f"{args.contact!r} is {res.status.replace('_', ' ')} among local contacts"
                    f"{': ' + res.question if res.question else ''}; add or pick the contact first",
                    "Ambiguous" if res.status == "ambiguous" else "NotFound",
                )
            target = res.best
        if not target.contact_id:
            raise ToolError("aliases can only point at saved contacts; use contacts.add first", "NotFound")
        key = resolver.set_alias(args.alias, target.contact_id)
        return self.ok(
            f"'{key}' now means {target.label()}", {"alias": key, "contact_id": target.contact_id, "name": target.name}
        )

    async def verify(self, args: AliasInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        stored = get_resolver(ctx.services).alias_target(normalise_alias(args.alias))
        return VerificationResult.from_checks([Check(name="alias stored", passed=stored == result.data["contact_id"])])


TOOLS: list[type[Tool]] = [ContactsResolve, ContactsAdd, ContactsAlias]
