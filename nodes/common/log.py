"""Logging with the node's name on every line, coloured on a terminal."""

from __future__ import annotations

import logging
import os
import sys

_COLORS = {"DEBUG": "\033[2m", "INFO": "", "WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[31;1m"}
_RESET = "\033[0m"


class _Formatter(logging.Formatter):
    def __init__(self, node: str, color: bool):
        super().__init__(datefmt="%H:%M:%S")
        self.node = node
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt) + f".{int(record.msecs):03d}"
        line = f"{ts} [{self.node}] {record.levelname:<7} {record.name}: {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if self.color:
            line = f"{_COLORS.get(record.levelname, '')}{line}{_RESET}"
        return line


def setup(node: str, level: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter(node, color=sys.stdout.isatty()))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level or os.environ.get("LANDCHAIN_LOG", "INFO"))
    # The HTTP plumbing is noisy at INFO; keep it quiet unless debugging.
    for noisy in ("httpx", "httpcore", "uvicorn.access", "uvicorn.error", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)
