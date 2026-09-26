"""Attach communication integrations (email, messaging, calendar, contacts) to the service container."""

from __future__ import annotations

from typing import Any


def attach(services: Any) -> None:
    from scar.integrations.calendar import CalendarService
    from scar.integrations.email import EmailService
    from scar.integrations.messaging import MessagingService
    from scar.tools.contacts.resolver import ContactResolver

    settings = services.settings
    services.email = EmailService(settings, services.secrets)
    services.messaging = MessagingService(settings, services.secrets)
    services.calendar = CalendarService(settings, services.secrets, services.db)
    services.contacts = ContactResolver(services.db, email=services.email)
