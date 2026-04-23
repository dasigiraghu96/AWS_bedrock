"""Unified CLI logging. Keeps boto3/urllib3 quiet by default."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-5s %(name)s  %(message)s"


def configure(verbose: bool = False, json_only: bool = False) -> None:
    """Configure the root logger.

    If json_only is True, logs go to stderr so stdout can stay pure JSON.
    """
    level = logging.DEBUG if verbose else logging.INFO
    stream = sys.stderr if json_only else sys.stdout

    # Avoid duplicate handlers if configure() is called more than once.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.setLevel(level)
    root.addHandler(handler)

    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
