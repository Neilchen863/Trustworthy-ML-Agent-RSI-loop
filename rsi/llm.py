"""LLM clients for the improver (stdlib only, so it runs on a bare CRC login node)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

PRICES_PER_M = {"gpt-4o": (2.5, 10.0), "gpt-4o-mini": (0.15, 0.6), "gpt-5": (1.25, 10.0)}   # list USD per 1M in/out


class LLMError(RuntimeError):
    pass


class OpenAIChat:
    def __init__(self, model="gpt-4o-2024-08-06", temperature=0.0, max_cost_usd=1.0, timeout=180, retries=3):
        self.model, self.temperature, self.max_cost_usd = model, temperature, max_cost_usd
        self.timeout, self.retries = timeout, retries
        self.calls = self.tokens_in = self.tokens_out = 0

    @property
    def cost_usd(self) -> float:
        best = max((p for p in PRICES_PER_M if self.model.startswith(p)), key=len, default=None)
        pin, pout = PRICES_PER_M[best] if best else (15.0, 60.0)        # unknown model: priced high on purpose
        return (self.tokens_in * pin + self.tokens_out * pout) / 1e6

    def chat(self, messages: list, tools: list) -> dict:
        if self.cost_usd >= self.max_cost_usd:
            raise LLMError(f"cost cap ${self.max_cost_usd:.2f} reached (${self.cost_usd:.3f})")
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise LLMError("OPENAI_API_KEY is not set (on CRC: source the run.env that holds it)")
        body = json.dumps({"model": self.model, "temperature": self.temperature, "messages": messages,
                           "tools": tools, "tool_choice": "auto"}).encode()
        last = None
        for attempt in range(self.retries):
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body, method="POST",
                                         headers={"Authorization": f"Bearer {key}",
                                                  "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                usage = data.get("usage") or {}
                self.calls += 1
                self.tokens_in += int(usage.get("prompt_tokens", 0))
                self.tokens_out += int(usage.get("completion_tokens", 0))
                return data["choices"][0]["message"]
            except urllib.error.HTTPError as exc:
                last = f"HTTP {exc.code}: {exc.read().decode(errors='ignore')[:200]}"
                if exc.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(2 ** attempt)
        raise LLMError(f"improver call failed: {last}")


class ScriptedLLM:
    """Replays fixed turns offline.  Each turn is a list of (tool_name, args) or a plain text reply."""
    model, cost_usd, calls = "scripted", 0.0, 0

    def __init__(self, turns):
        self.turns, self.seen = list(turns), []

    def chat(self, messages, tools):
        self.seen.append(messages)
        self.calls += 1
        if not self.turns:
            return {"role": "assistant", "content": "done"}
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return {"role": "assistant", "content": turn}
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{self.calls}_{i}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}} for i, (name, args) in enumerate(turn)]}
