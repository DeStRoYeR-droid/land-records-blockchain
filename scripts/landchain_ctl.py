#!/usr/bin/env python3
"""
landchain_ctl.py -- start, stop and inspect every LandChain process.

Normally used through the wrappers in the repo root:

    ./initial_setup.sh [--force] [--no-web]   once: certificates, genesis, channel, agent key
    ./startup.sh [--no-demo] [--no-web]       start everything that isn't running (+ demo)
    ./shutdown.sh [--clean]                   stop everything (or wipe all generated state)
    ./landchain.sh status                     who is running, Raft leader, peer heights
    ./landchain.sh restart peer0.org2         restart one process after editing its code
    ./landchain.sh kill orderer1              crash a node on purpose (watch Raft re-elect)
    ./landchain.sh logs agent                 follow one process's log

Each node is a plain OS process: PID in run/<name>.pid, output in
logs/<name>.log, data in data/<name>/. Nothing runs in containers, so you can
edit any node's Python and restart just that node.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from nodes.common import config  # noqa: E402

RUN = ROOT / "run"
LOGS = ROOT / "logs"
INIT_MARKER = config.ORGS_DIR / ".initialised"
PY = sys.executable

BOLD, DIM, RED, GREEN, YELLOW, BLUE, CYAN, RESET = (
    ("\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[34m", "\033[36m", "\033[0m")
    if sys.stdout.isatty() else ("",) * 8)


def banner(text: str) -> None:
    line = "=" * 66
    print(f"\n{BOLD}{BLUE}{line}\n  {text}\n{line}{RESET}")


def step(num: str, text: str) -> None:
    print(f"\n{BOLD}{CYAN}[{num}] {text}{RESET}")


def ok(text: str) -> None:
    print(f"  {GREEN}OK{RESET}  {text}")


def info(text: str) -> None:
    print(f"  {text}")


def warn(text: str) -> None:
    print(f"  {YELLOW}WARN{RESET} {text}")


def fail(text: str) -> None:
    print(f"  {RED}FAIL{RESET} {text}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Process table
# ---------------------------------------------------------------------------

@dataclass
class Proc:
    name: str
    group: str
    cmd: list[str]
    health: str | None
    cwd: Path = ROOT
    env: dict[str, str] = field(default_factory=dict)
    marker: str = ""

    @property
    def port(self) -> int:
        return config.node(self.name)["port"]


def processes() -> list[Proc]:
    procs: list[Proc] = []
    for name, spec in config.topology()["nodes"].items():
        kind = spec["kind"]
        procs.append(Proc(name, kind, [PY, "-m", f"nodes.{kind}", "--name", name],
                          f"{config.url(name)}/health", marker=f"--name {name}"))
    order = {"ca": 0, "orderer": 1, "peer": 2}
    procs.sort(key=lambda p: (order[p.group], p.name))
    svc = config.topology()["services"]
    procs.append(Proc("digilocker", "service",
                      [PY, "-m", "uvicorn", "app:app", "--app-dir", str(ROOT / "digilocker"),
                       "--host", svc["digilocker"].get("bind", "127.0.0.1"), "--port", str(svc["digilocker"]["port"]),
                       "--log-level", "warning"],
                      f"{config.url('digilocker')}/health", marker="app:app --app-dir"))
    if (ROOT / "agent" / "__main__.py").exists():
        procs.append(Proc("agent", "service", [PY, "-m", "agent"], f"{config.url('agent')}/health", marker="-m agent"))
    if (ROOT / "gateway" / "__main__.py").exists():
        procs.append(Proc("gateway", "service", [PY, "-m", "gateway"], f"{config.url('gateway')}/health",
                          marker="-m gateway"))
    web = ROOT / "web"
    if (web / "package.json").exists():
        web_spec = svc["web"]
        site = os.environ.get("NEXT_PUBLIC_SITE_URL", f"http://localhost:{web_spec['port']}")
        # only the Next.js server talks to the gateway; browsers use /gw on the web app's own origin
        gateway_url = os.environ.get("LANDCHAIN_GATEWAY_URL", f"http://127.0.0.1:{svc['gateway']['port']}")
        mode = os.environ.get("LANDCHAIN_WEB_MODE", "dev")  # dev (hot reload) or start (after npm run build)
        cmd = ["npm", "run", mode, "--", "-p", str(web_spec["port"]), "-H", web_spec.get("bind", "0.0.0.0")]
        procs.append(Proc("web", "web", cmd, f"http://127.0.0.1:{web_spec['port']}/api/health", cwd=web,
                          env={"NEXT_PUBLIC_SITE_URL": site, "LANDCHAIN_GATEWAY_URL": gateway_url,
                               "PORT": str(web_spec["port"]), "NEXT_TELEMETRY_DISABLED": "1"},
                          marker="next"))
    return procs


def find(name: str) -> Proc:
    for p in processes():
        if p.name == name:
            return p
    names = ", ".join(p.name for p in processes())
    fail(f"unknown process '{name}'. Known: {names}")
    raise SystemExit  # unreachable


def pid_of(p: Proc) -> int | None:
    pidfile = RUN / f"{p.name}.pid"
    if not pidfile.exists():
        return None
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError, PermissionError):
        pidfile.unlink(missing_ok=True)
        return None
    return pid


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def healthy(p: Proc) -> bool:
    if not p.health:
        return pid_of(p) is not None
    try:
        return httpx.get(p.health, timeout=1.5).status_code == 200
    except Exception:
        return False


def tail(path: Path, n: int = 25) -> str:
    if not path.exists():
        return "(no log)"
    return "\n".join(path.read_text(errors="replace").splitlines()[-n:])


def env_file() -> dict[str, str]:
    """landchain.env in the repo root: KEY=VALUE lines (e.g. OMLX_API_KEY=...)."""
    path = ROOT / "landchain.env"
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def child_env(p: Proc) -> dict[str, str]:
    env = {**env_file(), **os.environ}  # real environment variables win over the file
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LANDCHAIN_ROOT"] = str(ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    env.update(p.env)
    return env


def start_proc(p: Proc, wait: float = 60) -> None:
    if pid_of(p):
        if healthy(p):
            info(f"{DIM}{p.name:<12} already running{RESET}")
            return
    elif p.group != "web" and port_open(p.port):
        fail(f"port {p.port} for {p.name} is in use by another program -- free it and retry "
             f"(lsof -nP -iTCP:{p.port} -sTCP:LISTEN)")
    RUN.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    logf = open(LOGS / f"{p.name}.log", "ab")
    logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} starting {p.name}: {' '.join(p.cmd)} =====\n".encode())
    logf.flush()
    proc = subprocess.Popen(p.cmd, cwd=p.cwd, env=child_env(p), stdout=logf, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    (RUN / f"{p.name}.pid").write_text(str(proc.pid))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            fail(f"{p.name} exited during startup (code {proc.returncode}). Last log lines:\n"
                 f"{tail(LOGS / f'{p.name}.log')}")
        if healthy(p):
            ok(f"{p.name:<12} pid {proc.pid:<7} {DIM}{p.health.rsplit('/', 1)[0] if p.health else ''}{RESET}")
            return
        time.sleep(0.3)
    fail(f"{p.name} did not become healthy within {wait:.0f}s. Last log lines:\n{tail(LOGS / f'{p.name}.log')}")


def stop_proc(p: Proc, sig: int = signal.SIGTERM, grace: float = 8.0) -> bool:
    pid = pid_of(p)
    if not pid:
        return False
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, sig)
    except ProcessLookupError:
        (RUN / f"{p.name}.pid").unlink(missing_ok=True)
        return False
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    (RUN / f"{p.name}.pid").unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def preflight() -> None:
    if sys.version_info < (3, 10):
        fail(f"Python 3.10+ is required (this is {sys.version.split()[0]}). macOS: brew install python@3.12")
    ok(f"Python {sys.version.split()[0]} ({PY})")


def cmd_setup(args: argparse.Namespace) -> None:
    banner("LandChain -- initial setup")
    t0 = time.time()
    step("1/9", "Pre-flight checks")
    preflight()
    if INIT_MARKER.exists():
        if not args.force:
            warn("The network is already set up.")
            info("  ./startup.sh              start it and run the demo transactions")
            info("  ./initial_setup.sh --force  wipe everything and start over")
            return
        warn("--force: wiping the existing network first")
        cmd_clean(argparse.Namespace(yes=True))
    elif config.ORGS_DIR.exists() or (ROOT / "data").exists():
        warn("found leftovers of an earlier, incomplete setup -- cleaning them first")
        cmd_clean(argparse.Namespace(yes=True), quiet=True)
    busy = [p for p in processes() if p.group != "web" and port_open(p.port)]
    if busy:
        fail("ports already in use: " + ", ".join(f"{p.port} ({p.name})" for p in busy))
    ok("ports 13050-13950 are free")

    import network_admin  # noqa: E402  (sibling module)

    procs = processes()
    step("2/9", "Starting the four Certificate Authorities")
    for p in procs:
        if p.group == "ca":
            start_proc(p)

    step("3/9", "Issuing X.509 certificates (CSR enrollment; private keys never leave this machine)")
    network_admin.issue_identities(say=info)

    step("4/9", "Building the genesis block")
    network_admin.build_genesis(say=info)

    step("5/9", "Starting 3 Raft orderers and 3 peers")
    for p in procs:
        if p.group in ("orderer", "peer"):
            start_proc(p)

    step("6/9", f"Creating channel '{config.channel_name()}' and joining every node")
    network_admin.join_channel(say=info)

    step("7/9", "Smoke test -- the chaincode reads the caller's certificate")
    who = smoke_test()
    info(f"clerk1 -> {who}")
    ok("chaincode landrec is live on all three peers")

    step("8/9", "Starting DigiLocker, the Agentic AI Validator and the gateway")
    for p in procs:
        if p.group == "service":
            start_proc(p, wait=90)
    if any(p.name == "agent" for p in procs):
        attest_agent_key()
    attest_records_key()

    step("9/9", "Web app")
    setup_web(skip=args.no_web)

    INIT_MARKER.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    el = int(time.time() - t0)
    banner(f"Setup complete in {el // 60}m {el % 60}s")
    print_summary()


def smoke_test() -> str:
    import asyncio
    import json

    from nodes.sdk import Gateway

    async def run():
        gw = Gateway.for_user("Org1MSP", "clerk1")
        try:
            for _ in range(20):
                try:
                    return json.loads(await gw.evaluate("landrec", "WhoAmI"))
                except Exception:
                    await asyncio.sleep(0.5)
            raise RuntimeError("chaincode did not answer WhoAmI")
        finally:
            await gw.close()

    who = asyncio.run(run())
    return f"{{name: {who['name']}, mspId: {who['mspId']}, role: {who['role']}, dept: {who['dept']}}}"


def attest_agent_key() -> None:
    """A human Revenue officer (verifier1) attests the agent's PGP key on the ledger."""
    try:
        status = httpx.get(f"{config.url('agent')}/status", timeout=5).json()
    except Exception as exc:
        warn(f"could not read the agent's key ({exc}); run ./landchain.sh attest-agent later")
        return
    import asyncio

    from nodes.sdk import Gateway

    ident = status["identity"]

    async def run():
        gw = Gateway.for_user("Org2MSP", "verifier1")
        try:
            return await gw.submit_json("landrec", "RegisterAgentKey", ident["name"], ident["pgpFingerprint"],
                                        ident["did"], ident["publicKey"])
        finally:
            await gw.close()

    rec = asyncio.run(run())
    ok(f"verifier1 attested {rec['agentName']}'s PGP key {rec['pgpFingerprint']} on the ledger")


def attest_records_key(quiet: bool = False) -> None:
    """The Collector (collector1) attests the Records Office key that signs downloadable transfer records."""
    try:
        info_ = httpx.get(f"{config.url('gateway')}/api/public/records-key", timeout=8).json()
    except Exception as exc:
        warn(f"could not read the Records Office key ({exc}); run ./landchain.sh attest-records later")
        return
    if info_.get("onLedger"):
        if not quiet:
            ok(f"Records Office key {info_['pgpFingerprint']} already attested by {info_['onLedger']['attestedBy']}")
        return
    import asyncio

    from nodes.sdk import Gateway

    async def run():
        gw = Gateway.for_user("Org3MSP", "collector1")
        try:
            return await gw.submit_json("landrec", "RegisterRecordsKey", info_["pgpFingerprint"], info_["publicKey"])
        finally:
            await gw.close()

    rec = asyncio.run(run())
    ok(f"collector1 attested the Records Office key {rec['pgpFingerprint']} on the ledger")


def setup_web(skip: bool) -> None:
    web = ROOT / "web"
    if skip or not (web / "package.json").exists():
        info("skipped (--no-web)" if skip else "no web/ directory")
        return
    if not shutil.which("npm"):
        warn("npm not found -- install Node.js 20+ to run the dashboards (brew install node)")
        return
    if (web / "node_modules").exists():
        ok("web/node_modules present")
        return
    info("npm install (first time takes a minute) ...")
    rc = subprocess.call(["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=web)
    if rc != 0:
        warn("npm install failed -- run it yourself in web/ and then ./startup.sh")
    else:
        ok("web dependencies installed")


def cmd_start(args: argparse.Namespace) -> None:
    banner("LandChain -- startup")
    preflight()
    if not INIT_MARKER.exists():
        fail("The network has not been set up yet. Run ./initial_setup.sh first.")
    step("1/3", "Starting processes")
    for p in processes():
        if p.group == "web":
            if args.no_web:
                continue
            if not (p.cwd / "node_modules").exists():
                warn("web/node_modules missing -- run: cd web && npm install   (skipping the web app)")
                continue
            start_proc(p, wait=120)
        else:
            start_proc(p, wait=90)
    step("2/3", "Waiting for the network to be ready")
    import network_admin  # noqa: E402

    leader, _ = network_admin.raft_leader(timeout=30)
    if not leader:
        fail("no Raft leader -- check logs/orderer*.log")
    ok(f"Raft leader: {leader}")
    who = smoke_test()
    ok(f"chaincode answering ({who})")
    attest_records_key(quiet=True)
    step("3/3", "Network status")
    cmd_status(argparse.Namespace(), header=False)
    if not args.no_demo:
        rc = subprocess.call([PY, str(ROOT / "scripts" / "demo_transactions.py")], cwd=ROOT, env=child_env(processes()[0]))
        if rc != 0:
            sys.exit(rc)
    else:
        print()
        info("Network is up. Run ./demo_transactions.sh whenever you're ready.")
    print_summary(short=True)


def cmd_stop(_args: argparse.Namespace) -> None:
    banner("LandChain -- shutdown (data preserved)")
    stopped = 0
    for p in reversed(processes()):
        if stop_proc(p):
            ok(f"stopped {p.name}")
            stopped += 1
    if not stopped:
        info("nothing was running")
    info("Ledgers, certificates and keys are kept. Resume with: ./startup.sh")


def cmd_clean(args: argparse.Namespace, quiet: bool = False) -> None:
    if not quiet:
        banner("LandChain -- FULL RESET")
    if not args.yes:
        print("  This deletes every ledger, certificate, key and log.")
        if input("  Type 'yes' to continue: ").strip() != "yes":
            info("aborted")
            return
    for p in reversed(processes()):
        stop_proc(p, grace=4)
    for d in (config.ORGS_DIR, config.ARTIFACTS_DIR, ROOT / "data", RUN, LOGS):
        if d.exists():
            shutil.rmtree(d)
    if not quiet:
        ok("organizations/, channel-artifacts/, data/, run/ and logs/ deleted")
        info("Set up again with: ./initial_setup.sh")


def cmd_status(_args: argparse.Namespace, header: bool = True) -> None:
    if header:
        banner("LandChain -- status")
    print(f"  {'PROCESS':<12} {'STATE':<10} {'PID':<7} {'PORT':<6} DETAIL")
    for p in processes():
        pid = pid_of(p)
        up = healthy(p) if pid else False
        state = f"{GREEN}up{RESET}      " if up else (f"{YELLOW}starting{RESET}  " if pid else f"{DIM}down{RESET}    ")
        detail = ""
        if up and p.group in ("orderer", "peer"):
            try:
                st = httpx.get(f"{config.url(p.name)}/status", timeout=2).json()
                if p.group == "orderer":
                    r = st.get("raft") or {}
                    detail = f"{r.get('state', '?'):<9} term {r.get('term')}  height {st.get('height')}"
                    if r.get("state") == "leader":
                        detail = f"{BOLD}{detail}{RESET}"
                else:
                    cc = (st.get("chaincodes") or {}).get("landrec", {})
                    detail = (f"height {st.get('height')}  keys {st.get('worldStateKeys')}  "
                              f"landrec seq {cc.get('sequence')} {'ok' if cc.get('matches') else 'MISMATCH'}")
            except Exception:
                detail = "?"
        elif up and p.name == "agent":
            try:
                st = httpx.get(f"{config.url('agent')}/status", timeout=2).json()
                detail = f"explainer {st['explainer']['engine']}  runs {st['counters'].get('runs', 0)}"
            except Exception:
                detail = "?"
        port = str(p.port) if p.group != "web" else str(config.node("web")["port"])
        print(f"  {p.name:<12} {state} {str(pid or '-'):<7} {port:<6} {detail}")


def cmd_restart(args: argparse.Namespace) -> None:
    p = find(args.name)
    if stop_proc(p):
        ok(f"stopped {p.name}")
    start_proc(p, wait=120 if p.group == "web" else 90)


def cmd_kill(args: argparse.Namespace) -> None:
    p = find(args.name)
    if stop_proc(p, sig=signal.SIGKILL, grace=2):
        ok(f"killed {p.name} (SIGKILL -- no graceful shutdown). Bring it back with: ./landchain.sh restart {p.name}")
    else:
        info(f"{p.name} was not running")


def cmd_logs(args: argparse.Namespace) -> None:
    path = LOGS / f"{find(args.name).name}.log"
    if not path.exists():
        fail(f"no log yet at {path}")
    os.execvp("tail", ["tail", "-n", "60", "-f", str(path)])


def cmd_demo(_args: argparse.Namespace) -> None:
    sys.exit(subprocess.call([PY, str(ROOT / "scripts" / "demo_transactions.py")], cwd=ROOT,
                             env=child_env(processes()[0])))


def cmd_attest(_args: argparse.Namespace) -> None:
    attest_agent_key()


def cmd_attest_records(_args: argparse.Namespace) -> None:
    attest_records_key()


def cmd_geo_rebuild(_args: argparse.Namespace) -> None:
    """Delete the geospatial database and let the gateway rebuild it from the ledger."""
    path = config.data_dir("geo") / "landchain.gpkg"
    gw = next(p for p in processes() if p.name == "gateway")
    stop_proc(gw)
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    start_proc(gw, wait=60)
    for _ in range(60):
        try:
            st = httpx.get(f"{config.url('gateway')}/api/public/geo/stats", timeout=3).json()
            if st.get("syncedToBlock") is not None:
                ok(f"rebuilt {path.name} from the ledger: {st['parcels']} parcels, index: {st['spatialIndex']}")
                return
        except Exception:
            pass
        time.sleep(1)
    warn("the gateway is up but has not finished indexing yet; see ./landchain.sh logs gateway")


def cmd_check(_args: argparse.Namespace) -> None:
    """One complete transfer through the gateway's REST API, checked step by step."""
    sys.exit(subprocess.call([PY, str(ROOT / "scripts" / "check_gateway.py")], cwd=ROOT,
                             env=child_env(processes()[0])))


def cmd_check_agent(_args: argparse.Namespace) -> None:
    """The LangGraph validator against a misbehaving stand-in model (redraft, fallback, injection, kill and resume)."""
    sys.exit(subprocess.call([PY, str(ROOT / "scripts" / "check_agent.py")], cwd=ROOT, env=child_env(processes()[0])))


def cmd_check_web(_args: argparse.Namespace) -> None:
    """The web app's public pages, metadata and platform files over HTTP."""
    sys.exit(subprocess.call([PY, str(ROOT / "scripts" / "check_web.py")], cwd=ROOT, env=child_env(processes()[0])))


def print_summary(short: bool = False) -> None:
    gw = config.url("gateway")
    web_port = config.node("web")["port"]
    lines = [
        f"  Dashboards      http://localhost:{web_port}            (officials, citizens, agent)",
        f"  Gateway API     {gw}/docs",
        f"  Agent           {config.url('agent')}/docs",
        f"  DigiLocker      {config.url('digilocker')}/docs        (demo OTP 123456)",
        f"  A peer          {config.url('peer0.org1')}/docs",
    ]
    if not short:
        lines = [
            "  Organisations   Org1MSP Registration | Org2MSP Revenue | Org3MSP Collectorate",
            "  Orderers        3-node Raft (orderer1..3)        Channel  landchannel",
            "  Chaincode       landrec  policy AND(Org1, Org2, Org3)",
        ] + lines + [
            "",
            "  Next:",
            "    ./startup.sh            run the automated land-transfer transactions",
            "    ./landchain.sh status   see the Raft leader and peer heights",
            "    ./shutdown.sh           stop (keeps ledgers and certificates)",
        ]
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-web", action="store_true")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("start")
    s.add_argument("--no-demo", action="store_true")
    s.add_argument("--no-web", action="store_true")
    s.set_defaults(fn=cmd_start)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    s = sub.add_parser("clean")
    s.add_argument("--yes", "-y", action="store_true")
    s.set_defaults(fn=cmd_clean)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    for name, fn in (("restart", cmd_restart), ("kill", cmd_kill), ("logs", cmd_logs)):
        s = sub.add_parser(name)
        s.add_argument("name")
        s.set_defaults(fn=fn)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    sub.add_parser("attest-agent").set_defaults(fn=cmd_attest)
    sub.add_parser("attest-records").set_defaults(fn=cmd_attest_records)
    sub.add_parser("geo-rebuild").set_defaults(fn=cmd_geo_rebuild)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("check-agent").set_defaults(fn=cmd_check_agent)
    sub.add_parser("check-web").set_defaults(fn=cmd_check_web)
    args = parser.parse_args()
    os.chdir(ROOT)
    args.fn(args)


if __name__ == "__main__":
    main()
