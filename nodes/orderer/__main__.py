"""python -m nodes.orderer --name orderer1"""

import argparse

from ..common import config, log
from ..common.web import run
from .server import build_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Raft ordering node")
    parser.add_argument("--name", required=True, help="orderer name from network/nodes.yaml, e.g. orderer1")
    args = parser.parse_args()
    log.setup(args.name)
    run(build_app(args.name), config.bind_host(args.name), config.node(args.name)["port"])


if __name__ == "__main__":
    main()
