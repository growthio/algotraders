# -*- encoding: utf-8 -*-

"""
On-Disk Cache for Broker Instrument Masters
---------------------------------------------

An instrument master is large. The Fyers NSE derivatives CSV alone runs
to tens of thousands of rows, and Kite's dump covers every exchange it
serves. Downloading that on every process start is slow, wasteful, and
for Kite it also consumes a rate limited API call.

Freshness here is **anchored to the trading date, not to a rolling time
to live**, and that distinction is the reason this module exists rather
than a generic TTL cache. Futures and options instrument tokens are
flushed by the exchanges at every expiry, so a cache written on Thursday
can be wrong on Friday morning even though it is only twelve hours old.
Comparing the stored trading date against the current one catches that;
a 24 hour TTL does not.

The cache never decides what to do about staleness. It reports what it
holds and how old it is, and :class:`BaseInstrumentResolver` applies the
degradation ladder, because "serve stale data with a warning" is a
policy decision that belongs with the caller.
"""

import os
import json
import shutil
import pathlib
import tempfile
import datetime as dt

from typing import Any, Dict, List, Optional, Union

from algotraders.core.clock import BaseClock
from algotraders.logger import getLogger
from algotraders.models.enums import Segment
from algotraders.models.instrument import Instrument

logger = getLogger(__name__)

DATA_SUFFIX = ".jsonl"
META_SUFFIX = ".meta.json"


class InstrumentCache:
    """
    Stores one instrument master per broker and segment.

    Data is written as JSON lines rather than a single JSON array so a
    partial read is detectable and a large file can be streamed rather
    than held twice in memory during parsing.

    :type  directory: Union[str, pathlib.Path]
    :param directory: Cache root. One subdirectory per broker is created
        beneath it.

    :type  clock: BaseClock
    :param clock: Time source, read through ``tradingDate`` so freshness
        is evaluated in IST rather than host local time.
    """

    def __init__(
        self, directory : Union[str, pathlib.Path], clock : BaseClock
    ) -> None:
        self.directory = pathlib.Path(directory)
        self.clock = clock


    def pathFor(self, brokerName : str, segment : Segment) -> pathlib.Path:
        """
        Location of the data file for one broker and segment.
        """

        return self.directory / brokerName / f"{segment.value}{DATA_SUFFIX}"


    def metadataPathFor(
        self, brokerName : str, segment : Segment
    ) -> pathlib.Path:
        """
        Location of the sidecar metadata file.
        """

        return self.directory / brokerName / f"{segment.value}{META_SUFFIX}"


    def metadata(
        self, brokerName : str, segment : Segment
    ) -> Optional[Dict[str, Any]]:
        """
        Read the sidecar describing a cached master.

        :rtype:   Optional[Dict[str, Any]]
        :returns: The stored metadata, or ``None`` when absent or
            unreadable. An unreadable sidecar is treated as no cache at
            all, since without it freshness cannot be established.
        """

        path = self.metadataPathFor(brokerName, segment)

        if not path.is_file():
            return None

        try:
            return json.loads(path.read_text(encoding = "utf-8"))
        except Exception as error:
            logger.warning(
                "unreadable instrument cache metadata %s: %s",
                path.name, type(error).__name__
            )

            return None


    def isFresh(self, brokerName : str, segment : Segment) -> bool:
        """
        Whether the cached master was written on the current trading
        date.

        :rtype:   bool
        :returns: ``True`` only when both files exist and the stored
            trading date equals today's in IST.
        """

        record = self.metadata(brokerName, segment)

        if record is None:
            return False

        if not self.pathFor(brokerName, segment).is_file():
            return False

        return record.get("tradingDate") == self.clock.tradingDate().isoformat()


    def read(
        self, brokerName : str, segment : Segment
    ) -> Optional[List[Instrument]]:
        """
        Load a cached master regardless of its age.

        Returning stale data deliberately: the resolver decides whether
        stale is acceptable, and a stale master is far better than none
        when a download has just failed.

        :rtype:   Optional[List[Instrument]]
        :returns: The cached instruments, or ``None`` when nothing
            usable is stored. A malformed line invalidates the whole
            file, since a silently truncated master would resolve some
            symbols and mysteriously fail others.
        """

        path = self.pathFor(brokerName, segment)

        if not path.is_file():
            return None

        instruments : List[Instrument] = []

        try:
            with path.open("r", encoding = "utf-8") as stream:
                for line in stream:
                    entry = line.strip()

                    if entry:
                        instruments.append(Instrument.model_validate_json(entry))
        except Exception as error:
            logger.warning(
                "discarding corrupt instrument cache %s: %s",
                path.name, type(error).__name__
            )

            return None

        return instruments


    def write(
        self, brokerName : str, segment : Segment,
        instruments : List[Instrument], sourceURL : Optional[str] = None
    ) -> None:
        """
        Replace the cached master for one broker and segment.

        Both files are written to temporaries and renamed into place, so
        a reader never sees data without matching metadata or a half
        written master.

        :type  sourceURL: Optional[str]
        :param sourceURL: Where the master came from, recorded so an
            operator can tell which URL produced a suspect cache.
        """

        folder = self.directory / brokerName
        folder.mkdir(parents = True, exist_ok = True)

        payload = "\n".join(
            instrument.model_dump_json() for instrument in instruments
        )

        record = {
            "brokerName" : brokerName,
            "segment" : segment.value,
            "tradingDate" : self.clock.tradingDate().isoformat(),
            "fetchedAt" : self.clock.now().isoformat(),
            "rowCount" : len(instruments),
            "sourceURL" : sourceURL
        }

        _writeAtomically(
            self.pathFor(brokerName, segment), payload, folder
        )
        _writeAtomically(
            self.metadataPathFor(brokerName, segment),
            json.dumps(record, indent = 2), folder
        )

        logger.info(
            "cached %d %s %s instruments", len(instruments), brokerName,
            segment.value
        )


    def invalidate(self, brokerName : str, segment : Segment) -> None:
        """
        Drop a cached master, forcing the next read to download.
        """

        for path in (
            self.pathFor(brokerName, segment),
            self.metadataPathFor(brokerName, segment)
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


    def clear(self, brokerName : Optional[str] = None) -> None:
        """
        Remove every cached master, for one broker or for all of them.

        :type  brokerName: Optional[str]
        :param brokerName: Broker to clear. Clears the whole cache when
            omitted.
        """

        target = (
            self.directory / brokerName if brokerName else self.directory
        )

        if target.is_dir():
            shutil.rmtree(target, ignore_errors = True)


    def age(
        self, brokerName : str, segment : Segment
    ) -> Optional[dt.timedelta]:
        """
        How long ago the cached master was fetched.

        Reported for diagnostics only. Freshness is decided on the
        trading date, never on this value.

        :rtype:   Optional[dt.timedelta]
        :returns: Elapsed time since the fetch, or ``None`` when no
            usable timestamp is recorded.
        """

        record = self.metadata(brokerName, segment)

        if record is None or not record.get("fetchedAt"):
            return None

        try:
            fetched = dt.datetime.fromisoformat(record["fetchedAt"])
        except ValueError:
            return None

        return self.clock.now() - fetched


    def __repr__(self) -> str:
        return f"InstrumentCache(directory={str(self.directory)!r})"


def _writeAtomically(
    path : pathlib.Path, payload : str, folder : pathlib.Path
) -> None:
    """
    Write text to a path via a temporary file and an atomic rename.
    """

    handle, temporary = tempfile.mkstemp(
        dir = str(folder), prefix = ".cache-", suffix = ".tmp"
    )

    try:
        with os.fdopen(handle, "w", encoding = "utf-8") as stream:
            stream.write(payload)

        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass

        raise


__all__ = ["InstrumentCache", "DATA_SUFFIX", "META_SUFFIX"]
