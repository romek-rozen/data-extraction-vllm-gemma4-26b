"""Klient OpenAI-compat dla vLLM z guided_json + thinking OFF.

Single-request synchronous API. Concurrency obsługiwany w skryptach przez
ThreadPoolExecutor (vLLM serwer batchuje requesty natywnie).

Retry-with-feedback (length/parse error): przy `finish_reason=length` lub
nieparsowalnym JSON-ie wykonujemy max_retries_quality kolejnych prób, każda
z dodatkowymi messages opisującymi co poszło nie tak + poprzedni output, żeby
model mógł skorygować swoje zachowanie. Każda kolejna próba ma niższą
temperaturę (×0.5) i większy budżet tokenów (×1.5).
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
        timeout: float = 300.0,
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
        max_retries_network: int = 2,
        max_retries_quality: int = 2,
    ) -> dict[str, Any]:
        """Wywołaj /v1/chat/completions z response_format json_schema (xgrammar).

        Zwraca dict z parsowanym JSON + metadanymi:
            {ok, parsed, raw, usage, latency_s, error, finish_reason, attempts}
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        cur_temp = temperature
        cur_max_tokens = max_tokens
        attempts = 0
        prev_content: str | None = None
        prev_err: str | None = None
        last_result: dict[str, Any] | None = None

        for quality_attempt in range(max_retries_quality + 1):
            attempts += 1
            if quality_attempt > 0:
                # Dodaj feedback do modelu
                feedback = self._build_feedback(prev_content, prev_err, cur_max_tokens)
                # assistant turn z poprzednim outputem (bezpieczne — content może być pusty)
                messages = messages + [
                    {"role": "assistant", "content": prev_content or ""},
                    {"role": "user", "content": feedback},
                ]
                cur_temp = max(cur_temp * 0.5, 0.1)
                cur_max_tokens = int(cur_max_tokens * 1.5)
                logger.warning(
                    f"Retry-with-feedback (attempt {quality_attempt + 1}): "
                    f"temp={cur_temp:.2f}, max_tokens={cur_max_tokens}, prev_err={prev_err}"
                )

            result = self._single_call(
                messages=messages,
                json_schema=json_schema,
                schema_name=schema_name,
                max_tokens=cur_max_tokens,
                temperature=cur_temp,
                top_p=top_p,
                top_k=top_k,
                repetition_penalty=repetition_penalty,
                max_retries_network=max_retries_network,
            )
            last_result = result

            if result["ok"]:
                result["attempts"] = attempts
                return result

            # Quality fail = length truncate lub parse error → retry-with-feedback
            err = result.get("error") or ""
            is_length = result.get("finish_reason") == "length"
            is_parse = err.startswith("json_parse") or err.startswith("truncated_at_max_tokens")
            is_http = err.startswith("http_")
            if is_http:
                # 4xx/5xx — retry-with-feedback nic nie da
                result["attempts"] = attempts
                return result
            if not (is_length or is_parse):
                # network error po wszystkich network retries → wyjdź
                result["attempts"] = attempts
                return result

            prev_content = result.get("raw") or ""
            prev_err = err

        # Wszystkie retries quality wyczerpane
        if last_result is not None:
            last_result["attempts"] = attempts
        return last_result or {
            "ok": False,
            "error": "no_result",
            "raw": None,
            "parsed": None,
            "usage": {},
            "latency_s": 0.0,
            "attempts": attempts,
        }

    @staticmethod
    def _build_feedback(prev_content: str | None, prev_err: str | None, new_max_tokens: int) -> str:
        """Komunikat zwrotny do modelu — co zrobił źle i jak ma poprawić."""
        prev_snippet = (prev_content or "")[-1500:]  # ostatnie 1500 znaków, gdzie był breakage
        return (
            "Your previous response failed validation. Details:\n\n"
            f"ERROR: {prev_err}\n\n"
            f"YOUR PREVIOUS OUTPUT (last 1500 chars, may be truncated):\n"
            f"```\n{prev_snippet}\n```\n\n"
            f"FIX REQUIRED:\n"
            f"- Return ONE complete and valid JSON object matching the schema.\n"
            f"- You now have a budget of {new_max_tokens} output tokens — fit within it.\n"
            f"- If the previous output was truncated (finish_reason=length), be MORE CONCISE: "
            f"keep entity names short and lemmatized; reduce the number of entities to fit; "
            f"drop secondary/redundant entities and keep only the most semantically important.\n"
            f"- If the previous output had a JSON parse error, fix it: ensure all strings are "
            f"properly escaped, all brackets/braces match, and there is no trailing text after the JSON.\n"
            f"- Do NOT include markdown fences, comments, or any text outside the JSON.\n"
        )

    def _single_call(
        self,
        messages: list[dict[str, str]],
        json_schema: dict,
        schema_name: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        repetition_penalty: float,
        max_retries_network: int,
    ) -> dict[str, Any]:
        """Pojedyncze wywołanie z retry tylko na timeout/network."""
        body = {
            "model": self.model,
            "messages": messages,
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
        for attempt in range(max_retries_network + 1):
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
                choice = data["choices"][0]
                content = choice["message"]["content"]
                finish_reason = choice.get("finish_reason")
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as e:
                    err = f"json_parse: {e}"
                    if finish_reason == "length":
                        err = f"truncated_at_max_tokens (finish_reason=length, last char {len(content)}): {e}"
                    return {
                        "ok": False,
                        "error": err,
                        "raw": content,
                        "parsed": None,
                        "usage": data.get("usage", {}),
                        "latency_s": latency,
                        "finish_reason": finish_reason,
                    }
                # Sukces parsowania — ale jeśli finish_reason=length, JSON może być formalnie OK
                # ale obcięty (np. w środku tablicy zamknięty przez xgrammar). Treat as quality fail.
                if finish_reason == "length":
                    return {
                        "ok": False,
                        "error": f"truncated_at_max_tokens (finish_reason=length, parsed but suspect)",
                        "raw": content,
                        "parsed": parsed,
                        "usage": data.get("usage", {}),
                        "latency_s": latency,
                        "finish_reason": finish_reason,
                    }
                return {
                    "ok": True,
                    "error": None,
                    "raw": content,
                    "parsed": parsed,
                    "usage": data.get("usage", {}),
                    "latency_s": latency,
                    "finish_reason": finish_reason,
                }
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = f"network_attempt_{attempt}: {e}"
                logger.warning(f"vLLM request failed ({last_err}), retry...")
                time.sleep(1.0 * (attempt + 1))
            except requests.HTTPError as e:
                return {
                    "ok": False,
                    "error": f"http_{r.status_code}: {e} | body: {r.text[:300]}",
                    "raw": None,
                    "parsed": None,
                    "usage": {},
                    "latency_s": time.perf_counter() - t0,
                    "finish_reason": None,
                }

        return {
            "ok": False,
            "error": last_err,
            "raw": None,
            "parsed": None,
            "usage": {},
            "latency_s": 0.0,
            "finish_reason": None,
        }
