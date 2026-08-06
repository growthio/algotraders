# -*- encoding: utf-8 -*-

"""
Pluggable Persistence for Broker Session Tokens
-------------------------------------------------

SEBI requires a fresh broker session each trading day, so something has
to hold the day's token between the morning login and every later
request. The original code did this with ``open("token").read()`` at the
call site, which had no expiry tracking, no permissions and no way for a
server to use anything else.

:class:`BaseTokenStore` is deliberately three synchronous methods. That
is small enough that a host implements one over its own PostgreSQL in
about thirty lines, and synchronous because token reads happen once per
session rather than per request, so an async interface would buy nothing
and force every implementer into an event loop.

:class:`FileTokenStore` is the development default and **only** that.
Two properties make it unsuitable for production: under multi worker
uvicorn each worker refreshes independently and races on the write, and
on Windows the ``0o600`` mode does not actually restrict other users
because NTFS permissions are ACL based. Both are documented on the class
rather than left for somebody to discover in an incident.

:NOTE: Token files are named by a hash of the client id rather than the
    client id itself, so a directory listing or a screenshot of a
    terminal does not disclose which trading account is configured.
"""

import os
import json
import hashlib
import pathlib
import tempfile

from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple, Union

from algotraders.config import RuntimeConfig, runtimeConfig
from algotraders.logger import getLogger, maskSecret
from algotraders.models.auth import TokenBundle

logger = getLogger(__name__)

TOKEN_FILE_SUFFIX = ".token"

# ? enough of the hash to make a collision between two accounts on one
# machine implausible, short enough to keep the filename readable
CLIENT_HASH_LENGTH = 12


def hashClientId(clientId : str) -> str:
    """
    Derive a short, stable, non reversible handle for a client id.

    Used in token filenames so the account number never appears on disk
    in clear.

    :type  clientId: str
    :param clientId: Broker client id or API key.

    :rtype:   str
    :returns: The leading hex characters of its SHA-256 digest.
    """

    digest = hashlib.sha256(clientId.encode("utf-8")).hexdigest()

    return digest[:CLIENT_HASH_LENGTH]


class BaseTokenStore(ABC):
    """
    Contract for persisting broker session tokens.

    Implement this to keep tokens wherever the host already keeps its
    secrets: a database table, Redis, or a cloud secret manager. Three
    methods, all synchronous.
    """

    @abstractmethod
    def read(
        self, brokerName : str, clientId : str
    ) -> Optional[TokenBundle]:
        """
        Load a stored bundle.

        :rtype:   Optional[TokenBundle]
        :returns: The bundle, or ``None`` when none is stored. A stored
            but expired bundle is still returned; deciding what to do
            about expiry belongs to the session, not the store.
        """

        pass


    @abstractmethod
    def write(self, bundle : TokenBundle) -> None:
        """
        Persist a bundle, replacing any existing one for the same
        broker and client.
        """

        pass


    @abstractmethod
    def delete(self, brokerName : str, clientId : str) -> None:
        """
        Remove a stored bundle. Deleting a missing bundle is not an
        error, so a logout path never needs to check first.
        """

        pass


class MemoryTokenStore(BaseTokenStore):
    """
    In-process token store, holding bundles in a dictionary.

    The default for tests, and a legitimate choice for a short lived
    script that authenticates once and exits. Nothing survives the
    process, so a long running service using this re-authenticates on
    every restart.
    """

    def __init__(self) -> None:
        self.bundles : Dict[Tuple[str, str], TokenBundle] = {}


    def read(
        self, brokerName : str, clientId : str
    ) -> Optional[TokenBundle]:
        """
        Return the stored bundle for the broker and client, if any.
        """

        return self.bundles.get((brokerName, clientId))


    def write(self, bundle : TokenBundle) -> None:
        """
        Store the bundle in memory.
        """

        self.bundles[(bundle.brokerName, bundle.clientId)] = bundle


    def delete(self, brokerName : str, clientId : str) -> None:
        """
        Drop the stored bundle, ignoring a missing entry.
        """

        self.bundles.pop((brokerName, clientId), None)


    def __repr__(self) -> str:
        return f"MemoryTokenStore(sessions={len(self.bundles)})"


class FileTokenStore(BaseTokenStore):
    """
    Token store backed by one JSON file per session.

    **Development default only.** Two limitations rule it out for a
    production web backend, and neither is fixable within the design:

        * Under multi worker uvicorn every worker holds its own view.
          Each refreshes independently at expiry and they race on the
          write, so workers can end up using different tokens and
          consuming several refreshes where one was needed.
        * The ``0o600`` mode is a POSIX concept. On Windows it does not
          restrict other users, because access is governed by ACLs that
          this class does not attempt to manage.

    Production hosts implement :class:`BaseTokenStore` over their own
    database, which also solves both problems for free.

    :type  directory: Union[str, pathlib.Path]
    :param directory: Where token files are written. Created on demand.

    :type  fileMode: int
    :param fileMode: POSIX mode for created files. Ignored on Windows.
    """

    def __init__(
        self, directory : Union[str, pathlib.Path],
        fileMode : int = 0o600
    ) -> None:
        self.directory = pathlib.Path(directory)
        self.fileMode = fileMode


    @classmethod
    def fromEnvironment(
        cls, config : Optional[RuntimeConfig] = None
    ) -> "FileTokenStore":
        """
        Build a store rooted at the configured token directory.

        :type  config: Optional[RuntimeConfig]
        :param config: Configuration to read. Loaded from the
            environment when omitted.

        :rtype:   FileTokenStore
        :returns: A store pointed at ``ALGOTRADERS_TOKEN_DIR``.
        """

        resolved = config or runtimeConfig()

        return cls(directory = resolved.tokenDirectory)


    def pathFor(self, brokerName : str, clientId : str) -> pathlib.Path:
        """
        Location of the token file for one broker session.

        :rtype:   pathlib.Path
        :returns: ``<directory>/<broker>-<clientHash>.token``.
        """

        return self.directory / (
            f"{brokerName}-{hashClientId(clientId)}{TOKEN_FILE_SUFFIX}"
        )


    def read(
        self, brokerName : str, clientId : str
    ) -> Optional[TokenBundle]:
        """
        Load and parse the stored bundle.

        A corrupt or unreadable file is treated as absent rather than
        fatal. A truncated token file should force a fresh login, not
        take down the service on its morning start-up.
        """

        path = self.pathFor(brokerName, clientId)

        if not path.is_file():
            return None

        try:
            record = json.loads(path.read_text(encoding = "utf-8"))

            return TokenBundle.fromStorage(record)
        except Exception as error:
            logger.warning(
                "discarding unreadable token file %s: %s",
                path.name, type(error).__name__
            )

            return None


    def write(self, bundle : TokenBundle) -> None:
        """
        Persist the bundle atomically.

        The file is written to a temporary name in the same directory
        and then renamed over the target. ``os.replace`` is atomic on
        both POSIX and Windows, so a concurrent reader sees either the
        old bundle or the new one, never a half written file.
        """

        self.directory.mkdir(parents = True, exist_ok = True)

        path = self.pathFor(bundle.brokerName, bundle.clientId)
        payload = json.dumps(bundle.toStorage(), indent = 2)

        handle, temporary = tempfile.mkstemp(
            dir = str(self.directory), prefix = ".token-", suffix = ".tmp"
        )

        try:
            with os.fdopen(handle, "w", encoding = "utf-8") as stream:
                stream.write(payload)

            self.__restrict__(temporary)
            os.replace(temporary, path)
        except BaseException:
            # ! never leave a temporary file holding a live token behind
            _removeQuietly(temporary)

            raise

        logger.debug(
            "stored %s token for %s until %s",
            bundle.brokerName, maskSecret(bundle.clientId),
            bundle.expiresAt.isoformat() if bundle.expiresAt else "unknown"
        )


    def delete(self, brokerName : str, clientId : str) -> None:
        """
        Remove the stored token file, ignoring a missing one.
        """

        _removeQuietly(self.pathFor(brokerName, clientId))


    def __restrict__(self, path : Union[str, pathlib.Path]) -> None:
        """
        Tighten file permissions where the platform supports it.

        Failure is logged rather than raised: an unreadable token is a
        worse outcome than a permissive one, and the caller has already
        been told this store is a development convenience.
        """

        try:
            os.chmod(path, self.fileMode)
        except OSError as error:
            logger.debug(
                "could not set mode on token file: %s", type(error).__name__
            )


    def __repr__(self) -> str:
        return f"FileTokenStore(directory={str(self.directory)!r})"


def _removeQuietly(path : Union[str, pathlib.Path]) -> None:
    """
    Delete a path, ignoring the case where it is already gone.
    """

    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as error:
        logger.debug("could not remove %s: %s", path, type(error).__name__)


__all__ = [
    "BaseTokenStore", "MemoryTokenStore", "FileTokenStore",
    "hashClientId", "TOKEN_FILE_SUFFIX", "CLIENT_HASH_LENGTH"
]
