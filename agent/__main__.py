"""python -m agent  -- run the Agentic AI Validator service."""

from nodes.common import config, log
from nodes.common.web import run

from .service import build_app


def main() -> None:
    log.setup("agent")
    spec = config.node("agent")
    run(build_app(), spec.get("bind", "127.0.0.1"), spec["port"])


if __name__ == "__main__":
    main()
