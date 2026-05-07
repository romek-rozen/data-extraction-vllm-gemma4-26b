"""Klient OpenAI-compat dla vLLM z guided_json + thinking OFF.

Single-request synchronous API. Concurrency obsługiwany w skryptach przez
ThreadPoolExecutor (vLLM serwer batchuje requesty natywnie).
"""

import json
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


class VLLMClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8001/v1",
        model: str = "/model",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict,
        max_tokens: int,
        schema_name: str = "extraction",
        temperature: float = 1.0,
        top_p: float = 0.95,
        top_k: int = 64,
        repetition_penalty: float = 1.0,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """Wywołaj /v1/chat/completions z response_format json_schema (xgrammar).

        Zwraca dict z parsowanym JSON + metadanymi:
            {ok, parsed, raw, usage, latency_s, error}
        Retry tylko na timeout/network — xgrammar zapewnia poprawność JSON.
        """
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "repetition_penalty": repetition_penalty,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            },
            "chat_template_kwargs": {"enable_thinking": False},
        }

        last_err = None
        for attempt in range(max_retries + 1):
            t0 = time.perf_counter()
            try:
                r = requests.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    timeout=self.timeout,
                )
                latency = time.perf_counter() - t0
                r.raise_for_status()
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as e:
                    return {
                        "ok": False,
                        "error": f"json_parse: {e}",
                        "raw": content,
                        "parsed": None,
                        "usage": data.get("usage", {}),
                        "latency_s": latency,
                    }
                return {
                    "ok": True,
                    "error": None,
                    "raw": content,
                    "parsed": parsed,
                    "usage": data.get("usage", {}),
                    "latency_s": latency,
                }
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = f"network_attempt_{attempt}: {e}"
                logger.warning(f"vLLM request failed ({last_err}), retry...")
                time.sleep(1.0 * (attempt + 1))
            except requests.HTTPError as e:
                # 4xx — nie ma sensu retry
                return {
                    "ok": False,
                    "error": f"http_{r.status_code}: {e} | body: {r.text[:300]}",
                    "raw": None,
                    "parsed": None,
                    "usage": {},
                    "latency_s": time.perf_counter() - t0,
                }

        return {
            "ok": False,
            "error": last_err,
            "raw": None,
            "parsed": None,
            "usage": {},
            "latency_s": 0.0,
        }
