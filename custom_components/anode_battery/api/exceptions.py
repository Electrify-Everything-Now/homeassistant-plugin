"""Exceptions raised by the Anode API client."""
from __future__ import annotations


class AnodeError(Exception):
    """Base class for every error raised by the client."""


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
