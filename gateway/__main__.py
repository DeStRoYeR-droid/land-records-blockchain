"""python -m gateway  -- the REST API for the dashboards (port 13900)."""

from nodes.common import config, log
from nodes.common.web import run

from .app import build_app


def main() -> None:
    log.setup("gateway")
    spec = config.node("gateway")
    run(build_app(), spec.get("bind", "0.0.0.0"), spec["port"])


if __name__ == "__main__":
    main()
