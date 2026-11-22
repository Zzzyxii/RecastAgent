from __future__ import annotations

import base64
import json
import os
import mimetypes
import random
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .settings import DEFAULT_MODEL, DEFAULT_BASE_URL, load_credentials

DEFAULT_OPENROUTER_MODEL = DEFAULT_MODEL
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_SITE_URL = "http://localhost"
DEFAULT_OPENROUTER_APP_NAME = "RecastAgent"
_DEFAULT_EFFORT = object()
# The shared gateway key allows 200 requests/min; 32 workers routinely exceed it.
RATE_LIMIT_RETRIES = 30
JSON_RETRIES = 4


def _valid_json_object(text):
    from planner_harness.json_output import decode_object
    try:
        decode_object(text)
        return True
    except ValueError:
        return False


def _post(url, *, headers, json, timeout):
    import json as codec

    request = urllib.request.Request(
        url, data=codec.dumps(json, ensure_ascii=False).encode(), headers=headers, method="POST"
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        try:
            payload = codec.loads(raw)
        except ValueError:
            payload = {}
        return response.status, payload


class APIError(RuntimeError):
    def __init__(self, status, message, *, code=None):
        self.http_status = status
        self.retryable = status in {408, 429, 500, 502, 503, 504}
        self.safe_diagnostic = {"status": status, "code": code, "message": message}
        super().__init__(f"OpenRouter HTTP {status}: {message}")


def extract_frames(video_path: str, *, frame_count: int = 4, width: int = 640) -> list[Path]:
    tmpdir = Path(tempfile.mkdtemp(prefix="openrouter_frames_"))
    out_pattern = tmpdir / "frame_%02d.jpg"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        f"fps=1,scale={width}:-1",
        "-frames:v",
        str(frame_count),
        str(out_pattern),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(tmpdir.glob("frame_*.jpg"))


def _image_to_data_url(path: Path) -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{payload}"


class OpenRouterVisionModel:
    is_live_api = True
    requires_live_budget = False

    def __init__(
        self,
        *,
        model: str | None = None,
        api_url: str | None = None,
        base_url: str | None = None,
        api_key_env: str = "OPENROUTER_API_KEY",
        credential_file: str | None = None,
        role: str = "planner",
        reasoning_effort=_DEFAULT_EFFORT,
        timeout: int = 3600,
        max_tokens: int | None = None,
        output_limit_mode: str = "provider_default",
        site_url: str | None = None,
        app_name: str | None = None,
        require_provider_parameters: bool = True,
        reasoning_transport: str = "openrouter",
        json_response: bool = False,
    ) -> None:
        load_credentials(credential_file)
        prefix = "RECAST_API_" + role.upper() + "_"
        self.role = role
        self.model = (
            model
            or os.environ.get(prefix + "MODEL")
            or os.environ.get("OPENROUTER_MODEL")
            or DEFAULT_MODEL
        )
        self.reasoning_effort = (
            (
                os.environ.get(prefix + "REASONING_EFFORT")
                or os.environ.get("OPENROUTER_REASONING_EFFORT", "high")
            )
            if reasoning_effort is _DEFAULT_EFFORT
            else reasoning_effort
        )
        endpoint = base_url or os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)
        self.base_url = endpoint.rstrip("/")
        self.api_url = api_url or endpoint.rstrip("/") + "/chat/completions"
        self.timeout = timeout
        self.max_tokens = max_tokens
        if output_limit_mode not in {"provider_default", "hard"}:
            raise ValueError("API output_limit_mode must be provider_default or hard")
        self.output_limit_mode = output_limit_mode
        if not isinstance(require_provider_parameters, bool):
            raise ValueError("require_provider_parameters must be a boolean")
        self.require_provider_parameters = require_provider_parameters
        if reasoning_transport not in {"openrouter", "openai_compatible"}:
            raise ValueError("reasoning_transport must be openrouter or openai_compatible")
        self.reasoning_transport = reasoning_transport
        if not isinstance(json_response, bool):
            raise ValueError("json_response must be a boolean")
        self.json_response = json_response
        self.site_url = (
            site_url or os.environ.get("OPENROUTER_SITE_URL") or DEFAULT_OPENROUTER_SITE_URL
        ).strip()
        self.app_name = (
            app_name or os.environ.get("OPENROUTER_APP_NAME") or DEFAULT_OPENROUTER_APP_NAME
        ).strip()
        self.api_key = os.environ.get(api_key_env, "").strip()
        if not self.api_key:
            raise RuntimeError(f"Missing OpenRouter API key in env var: {api_key_env}")

    def budget_capabilities(self):
        return {
            "hard_output_tokens": self.output_limit_mode == "hard" or self.max_tokens is not None,
            "hard_invoice": False,
            "output_limit_mode": self.output_limit_mode,
        }

    def runtime_identity(self):
        return {
            "backend": "openrouter_api",
            "endpoint": self.api_url,
            "provider_model": getattr(self, "api_model", self.model),
            "protocol": "chat-completions-v1",
            "reasoning_effort": self.reasoning_effort,
            "max_tokens": self.max_tokens,
            "output_limit_mode": self.output_limit_mode,
            "image_transport": "original-bytes-data-url",
            "model_fallback": False,
            "require_provider_parameters": self.require_provider_parameters,
            "reasoning_transport": self.reasoning_transport,
            "json_response": self.json_response,
        }

    def predict_text(
        self, *, system_prompt: str, user_text: str, image_paths: list[str], max_new_tokens=None
    ) -> str:
        from hashlib import sha256

        self.last_usage = {}
        self.last_provider_response = {}
        self.last_finish_reason = None
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for image_path in image_paths:
            content.append(
                {"type": "image_url", "image_url": {"url": _image_to_data_url(Path(image_path))}}
            )
        provider_model = getattr(self, "api_model", self.model)
        body = {
            "model": provider_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "stream": False,
        }
        # OpenRouter accepts this routing contract.  OpenAI-compatible gateways
        # such as LiteLLM reject unknown top-level fields, so their explicitly
        # frozen host configuration disables it without changing model/prompt
        # semantics or silently falling back to another provider.
        if self.require_provider_parameters:
            body["provider"] = {"require_parameters": True}
        if self.reasoning_effort and self.reasoning_transport == "openrouter":
            body["reasoning"] = {"effort": self.reasoning_effort, "exclude": True}
        elif self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if self.json_response:
            # Without it gpt-6-luna returns empty content on ~25% of turns.
            body["response_format"] = {"type": "json_object"}
            # JSON mode is rejected (HTTP 400) unless the messages mention "json".
            if "json" not in (system_prompt + user_text).lower():
                body["messages"][0]["content"] = system_prompt + "\n\nRespond with one JSON object."
        limit = (
            self.max_tokens
            if self.max_tokens is not None
            else max_new_tokens
            if self.output_limit_mode == "hard"
            else None
        )
        if self.output_limit_mode == "hard" and limit is None:
            raise ValueError("An explicit hard output limit requires max_tokens or max_new_tokens")
        if limit is not None:
            body["max_tokens"] = limit
        self.last_output_limit = {
            "requested_tokens": limit,
            "hard_enforced": limit is not None,
            "mode": "explicit" if limit is not None else "provider_default",
        }
        timeout = self.timeout
        binding = getattr(self, "recast_accounting_scope", None)
        if binding:
            from planner_harness import accounting
            from planner_harness.resources import TaskDeadline
            import time

            accounting.check_task(binding)
            ledger = accounting.load_ledger(binding["root"])
            task = (
                ledger.get("topics", {})
                .get(binding["topic"], {})
                .get("lanes", {})
                .get(binding.get("lane", "independent"), {})
            )
            if task.get("deadline_unix"):
                timeout = min(timeout, task["deadline_unix"] - time.time())
                if timeout <= 0:
                    raise TaskDeadline("Task deadline reached before API dispatch")
        self.last_client_audit = {
            "backend": "openrouter_api",
            "endpoint": self.api_url,
            "requested_model": provider_model,
            "reasoning_effort": self.reasoning_effort,
            "request_sha256": sha256(
                json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
            "image_count": len(image_paths),
            "output_limit": self.last_output_limit,
        }
        usage_total = {}
        for attempt in range(JSON_RETRIES):
            text = self._complete(body, timeout)
            for key, value in (self.last_usage or {}).items():
                if type(value) is int:
                    usage_total[key] = usage_total.get(key, 0) + value
            if not self.json_response or _valid_json_object(text):
                break
            # gpt-6-luna sometimes returns empty or non-JSON content; resend.
            self.last_client_audit["invalid_json_retries"] = attempt + 1
        self.last_usage = {**(self.last_usage or {}), **usage_total, "provider_responses": attempt + 1}
        self.last_provider_response["usage"] = self.last_usage
        return text

    def _complete(self, body, timeout):
        # A gateway 429 rejects before dispatch, so waiting and resending is safe;
        # it must not consume the call's few transport attempts.
        for _ in range(RATE_LIMIT_RETRIES):
            status, payload = self._send(body, timeout)
            if status != 429:
                break
            time.sleep(random.uniform(10, 40))
        if not isinstance(payload, dict):
            payload = {}
        if status >= 400 or payload.get("error"):
            error = payload.get("error") or {}
            if not isinstance(error, dict):
                error = {"message": str(error)}
            message = str(error.get("message", "Request failed"))
            message = re.sub(
                r"sk-[A-Za-z0-9_-]+", "[redacted]", message.replace(self.api_key, "[redacted]")
            )
            raise APIError(status if status >= 400 else 502, message[:1000], code=error.get("code"))
        choices = payload.get("choices") or []
        if not choices:
            raise APIError(502, "Provider returned no completion")
        choice = choices[0]
        text = choice.get("message", {}).get("content")
        if isinstance(text, list):
            text = "".join(x.get("text", "") for x in text if x.get("type") == "text")
        if not isinstance(text, str):
            text = ""
        self.last_usage = payload.get("usage") or {}
        self.last_provider_model = payload.get("model")
        self.last_finish_reason = choice.get("finish_reason")
        self.last_provider_response = {
            "id": payload.get("id"),
            "model": self.last_provider_model,
            "provider": payload.get("provider"),
            "usage": self.last_usage,
            "choices": [{"finish_reason": self.last_finish_reason}],
        }
        self.last_client_audit.update(
            response_id=payload.get("id"),
            returned_model=self.last_provider_model,
            provider=payload.get("provider"),
        )
        return text

    def _send(self, body, timeout):
        return _post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": self.site_url,
                "X-Title": self.app_name,
            },
            json=body,
            timeout=timeout,
        )

    def predict_json(
        self, *, system_prompt: str, user_text: str, image_paths: list[str]
    ) -> dict[str, Any]:
        from planner_harness.json_output import decode_object

        return decode_object(
            self.predict_text(
                system_prompt=system_prompt, user_text=user_text, image_paths=image_paths
            )
        )
