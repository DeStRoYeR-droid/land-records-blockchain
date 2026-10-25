"""Gateway settings (environment variables or landchain.env)."""

import os

from nodes.common import config

CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
DIGILOCKER_URL = os.environ.get("DIGILOCKER_URL", config.url("digilocker"))
DIGILOCKER_REVENUE_KEY = os.environ.get("DIGILOCKER_REVENUE_KEY", "revenue-demo-key")
AGENT_TOKEN = os.environ.get("AGENT_CONTROL_TOKEN", "landchain-dev-agent-token")
AGENT_NAME = os.environ.get("LANDCHAIN_AGENT_USER", "agent1")
SRO = os.environ.get("LANDCHAIN_SRO", "Sub-Registrar Office, Hanamkonda")
