"""Exceptions raised by the Anode API client."""
from __future__ import annotations


from typing import Any


class AnodeError(Exception):
    """Base class for every error raised by the client.

    Errors raised for an HTTP response carry its ``status`` and parsed JSON
    ``body``, so a caller that knows one endpoint's error shapes can read them.
    """

    def __init__(self, *args: object, status: int | None = None, body: Any = None) -> None:
        super().__init__(*args)
        self.status = status
        self.body = body


class AnodeConnectionError(AnodeError):
    """The Anode cloud could not be reached, or did not answer in time."""


class AnodeHubOfflineError(AnodeConnectionError):
    """The cloud is reachable but the hub did not answer (HTTP 408)."""


class AnodeRateLimitError(AnodeConnectionError):
    """The cloud asked us to slow down (HTTP 429)."""


class AnodeAuthError(AnodeError):
    """The email/API key pair was rejected (HTTP 401)."""


class AnodeForbiddenError(AnodeAuthError):
    """The credentials are valid but may not access this hub (HTTP 403)."""


class AnodeNotFoundError(AnodeError):
    """The hub or resource does not exist (HTTP 404)."""


class AnodeResponseError(AnodeError):
    """The cloud answered with something we could not interpret."""


class AnodeCommandError(AnodeError):
    """The hub received a command or read and reported that it failed."""


class AnodeUpdateInProgressError(AnodeError):
    """A firmware update is already running on the hub, so nothing started.

    ``device_id`` names the device updating when the server knows it. It does
    not when the refusal came from the hub itself.
    """

    def __init__(self, *args: object, device_id: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.device_id = device_id


class AnodeHubRefusedError(AnodeCommandError):
    """The hub refused a firmware update for a reason of its own.

    ``info`` is what the hub said, when it said anything.
    """

    def __init__(self, *args: object, info: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.info = info


class AnodeLinkDeniedError(AnodeError):
    """The account holder refused a link request."""


class AnodeLinkExpiredError(AnodeError):
    """A link code ran out, or was already used, before a key was collected."""


class AnodeLinkFailedError(AnodeError):
    """A link was approved but the key it issued could not be used.

    Never retried: collecting spends the code, so by the time this is raised
    the grant is gone and only a new link request can replace it.
    """
