"""python -m nodes.ca --name ca-org1"""

import argparse

from ..common import config, log
from ..common.web import run
from .server import build_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one organisation's Certificate Authority")
    parser.add_argument("--name", required=True, help="CA name from network/nodes.yaml, e.g. ca-org1")
    args = parser.parse_args()
    log.setup(args.name)
    spec = config.node(args.name)
    run(build_app(args.name), config.bind_host(args.name), spec["port"])


if __name__ == "__main__":
    main()
