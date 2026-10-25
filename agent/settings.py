"""
Validator settings, all overridable with environment variables (or landchain.env).

oMLX (https://github.com/jundot/omlx) serves MLX models on Apple Silicon with
an OpenAI-compatible API. Start it, load a small instruct model, then:

    OMLX_BASE_URL=http://127.0.0.1:8000/v1   # oMLX's default port
    OMLX_API_KEY=<the key you configured in oMLX>
    OMLX_MODEL=<model id from GET /v1/models>   # empty = first model listed

    LANDCHAIN_JUDGE=auto|on|off          second-opinion LLM judge of each explanation (advisory)
    LANDCHAIN_FAITHFULNESS_MIN=0.8       an explanation scoring below this is redrafted, then replaced
    LANDCHAIN_DRAFT_ATTEMPTS=2           model drafts per audience before the template takes over
    LANDCHAIN_AGENT_MAX_CALLS=5          tool calls the investigating agent may make per file
    LANDCHAIN_AGENT_TOOLS=auto           how tool calls travel: native OpenAI `tools`, or <tool_call> text
    OMLX_TEMPERATURE=0.2

LANDCHAIN_EXPLAINER=auto      use oMLX when reachable, else the template writer
                    omlx      require oMLX (fall back only on errors, logged)
                    template  never call a model
"""

from __future__ import annotations

import os

from nodes.common import config


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


OMLX_BASE_URL = env("OMLX_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
OMLX_API_KEY = env("OMLX_API_KEY", "")
OMLX_MODEL = env("OMLX_MODEL", "")
OMLX_TIMEOUT = float(env("OMLX_TIMEOUT", "120"))
OMLX_NO_THINK = env("OMLX_NO_THINK", "auto")  # auto: append /no_think for Qwen3-style models
OMLX_TEMPERATURE = float(env("OMLX_TEMPERATURE", "0.2"))
OMLX_STREAM_USAGE = env("OMLX_STREAM_USAGE", "auto")  # auto: ask for token usage, stop asking if the server refuses
EXPLAINER_MODE = env("LANDCHAIN_EXPLAINER", "auto")
JUDGE_MODE = env("LANDCHAIN_JUDGE", "auto")
FAITHFULNESS_MIN = float(env("LANDCHAIN_FAITHFULNESS_MIN", "0.8"))
DRAFT_ATTEMPTS = max(1, int(env("LANDCHAIN_DRAFT_ATTEMPTS", "2")))

# The agent loops (agentic.py): the investigation after the fixed checks, the Ask box
INVESTIGATE = env("LANDCHAIN_INVESTIGATE", "auto")          # auto (when the model is up) | off
AGENT_MAX_CALLS = max(1, int(env("LANDCHAIN_AGENT_MAX_CALLS", "5")))    # tool calls per investigation
AGENT_MAX_TURNS = max(2, int(env("LANDCHAIN_AGENT_MAX_TURNS", "7")))    # model turns per investigation
AGENT_TOOL_PROTOCOL = env("LANDCHAIN_AGENT_TOOLS", "auto")  # auto | native (OpenAI tools) | text (<tool_call> blocks)
REFLECT = env("LANDCHAIN_REFLECT", "auto")                  # the model reviews its rejected draft and picks the next step
GATEWAY_URL = env("LANDCHAIN_GATEWAY_URL", config.url("gateway"))

AGENT_USER = env("LANDCHAIN_AGENT_USER", "agent1")
AGENT_MSP = "Org2MSP"
CONTROL_TOKEN = env("AGENT_CONTROL_TOKEN", "landchain-dev-agent-token")
DIGILOCKER_URL = env("DIGILOCKER_URL", config.url("digilocker"))
DIGILOCKER_KEY = env("DIGILOCKER_VALIDATOR_KEY", "validator-demo-key")
DIGILOCKER_REQUESTER = "Revenue Validator Agent (Org2)"

DATA_DIR = config.data_dir("agent")
SOFTWARE = "landchain-validator/3.0 (langgraph, tool-calling agent)"

# Check thresholds (the rules, written down)
EXTENT_WARN_PCT = 5.0        # deed extent vs recorded area
EXTENT_FAIL_PCT = 15.0
VALUATION_LOW_RATIO = 0.5    # price/sq.m below half the district median -> attention
VALUATION_HIGH_RATIO = 3.0
VALUATION_MIN_COMPARABLES = 3
RAPID_RESALE_DAYS = 90
EC_MAX_AGE_YEARS = 1
