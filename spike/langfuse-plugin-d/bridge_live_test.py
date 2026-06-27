#!/usr/bin/env python3
"""Bridge-side Option-D LIVE test: drive the PRODUCTION Bridge code path against a
running plugin-ON gateway and confirm Langfuse traces the call at the id #6 re-derives.

Unlike verify_d_audio.py (which hand-builds the POST), this exercises the REAL
warehouse_llm_bridge.HermesClient.decide() -> _decide_plugin_owned (plain openai +
extra_headers={X-Hermes-Session-Id: H} + drift-detect). A non-Command response is
fine: the LLM call already happened, so the plugin minted the trace. This is a TEXT
commander call (the Bridge's actual modality); the audio->ER->Langfuse path is
covered separately by verify_d_audio.py.

Reads creds/base from env (set by run-bridge-live.sh). Prints no secret values.
Exit: 0 PASS / 1 FAIL.
"""
import asyncio
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

from eval_sdk.seed import derive_plugin_trace_id, seed_for
from warehouse_llm_bridge.hermes_client import (
    LANGFUSE_OWNER_HERMES_PLUGIN,
    HermesClient,
    LLMUnavailableError,
)

BASE = os.environ.get("MWR_GATEWAY_BASE", "http://127.0.0.1:8644")
API_KEY = os.environ.get("API_SERVER_KEY", "")
PUB = os.environ["HERMES_LANGFUSE_PUBLIC_KEY"].strip()
SEC = os.environ["HERMES_LANGFUSE_SECRET_KEY"].strip()
LF_BASE = (os.environ.get("HERMES_LANGFUSE_BASE_URL") or os.environ.get("HERMES_LANGFUSE_HOST")
           or "https://cloud.langfuse.com").strip().rstrip("/")

RUN_ID = os.environ.get("MWR_BRIDGE_RUN_ID", "bridgerun")
GEN_ID = os.environ.get("MWR_BRIDGE_GEN_ID", "g1")
H = seed_for(RUN_ID, GEN_ID)
expected = derive_plugin_trace_id(RUN_ID, GEN_ID)
print(f"[bridge-live] H = seed_for({RUN_ID!r},{GEN_ID!r}) = {H!r}")
print(f"[bridge-live] expected trace_id = derive_plugin_trace_id(...) = {expected}")

# --- drive the ACTUAL production Bridge code path (owner = hermes_plugin) ----------
client = HermesClient(BASE, api_key=API_KEY,
                      langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id=RUN_ID, timeout=60.0)
situation = {"gen_id": GEN_ID, "note": "bridge-live-langfuse-test", "robots": []}
print("[bridge-live] HermesClient.decide(owner=hermes_plugin) -> _decide_plugin_owned against live gateway")
try:
    cmd = asyncio.run(client.decide(situation))
    print(f"[bridge-live] decide() parsed a Command (bonus): {str(cmd)[:120]}")
except LLMUnavailableError as e:
    print(f"[bridge-live] FAIL: transport error, the call did not reach ER: {e}")
    sys.exit(1)
except ValueError as e:
    print(f"[bridge-live] response was not a Command JSON ({str(e)[:80]}) — fine, the call still traced")

# --- confirm the plugin minted the trace at the BRIDGE-derived id -----------------
_auth = base64.b64encode(f"{PUB}:{SEC}".encode()).decode()


def _get(path):
    req = urllib.request.Request(LF_BASE + path, headers={"Authorization": f"Basic {_auth}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001
        return None, None


for i in range(9):
    st, body = _get(f"/api/public/traces/{expected}")
    if st == 200:
        gens = [o for o in (body.get("observations") or []) if o.get("type") == "GENERATION"]
        print(f"[bridge-live] PASS ✅  trace {expected} landed (generations={len(gens)}) after ~{i*5}s")
        print("  => the PRODUCTION Bridge (decide -> _decide_plugin_owned: plain openai +")
        print("     X-Hermes-Session-Id=H) makes the plugin trace, and #6 re-derives the SAME")
        print("     id via derive_plugin_trace_id. Bridge Option-D Langfuse join verified LIVE.")
        sys.exit(0)
    print(f"  not visible yet ({i+1}/9, http={st}); waiting 5s")
    time.sleep(5)

print(f"[bridge-live] FAIL: trace {expected} not found within ~40s")
sys.exit(1)
