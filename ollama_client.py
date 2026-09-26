"""
ollama_client.py - Talk to the local LLM through Ollama.

Ollama runs on this computer as a small web server (default port 11434).
We talk to it with ordinary HTTP requests, the same way a program would
call a cloud AI API. The difference is that the request never leaves the
machine and costs nothing.

    get_status()    -> is Ollama running, and is our model downloaded?
    list_models()   -> models installed locally
    chat(...)       -> send messages, get the whole answer plus timing info
    chat_stream(...)-> same, but yields the answer piece by piece as it is
                       generated ("streaming"), so the user sees text at once

We use the plain `requests` library instead of an SDK so students can see
exactly what is sent and received.
"""

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass

import requests

from config import OllamaSettings, get_ollama_settings

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """A problem talking to Ollama, with a message that is safe to show."""


@dataclass
class ChatResult:
    content: str              # the model's answer text
    model: str
    elapsed_seconds: float    # total time for this request
    load_seconds: float       # time spent loading the model into memory
    prompt_tokens: int        # tokens we sent (prompt size)
    output_tokens: int        # tokens the model generated
    generation_seconds: float # time spent generating the answer

    @property
    def tokens_per_second(self) -> float:
        """Generation speed, as measured by Ollama itself."""
        if self.generation_seconds <= 0:
            return 0.0
        return self.output_tokens / self.generation_seconds


@dataclass
class OllamaStatus:
    running: bool
    version: str | None
    model: str
    model_available: bool
    message: str


def _full_name(model: str) -> str:
    """Ollama treats 'name' as 'name:latest'."""
    return model if ":" in model else f"{model}:latest"


def _get(settings: OllamaSettings, path: str, timeout: int = 5) -> dict:
    try:
        response = requests.get(f"{settings.url}{path}", timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.ConnectionError as exc:
        raise OllamaError(
            "Ollama is not running. Start the Ollama app (or run 'ollama serve')."
        ) from exc
    except requests.RequestException as exc:
        raise OllamaError(f"Could not talk to Ollama: {exc}") from exc


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def list_models(settings: OllamaSettings | None = None) -> list[dict]:
    """Return installed models as [{'name': ..., 'size_gb': ...}, ...]."""
    settings = settings or get_ollama_settings()
    data = _get(settings, "/api/tags")
    return [
        {"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1)}
        for m in data.get("models", [])
    ]


def get_status(settings: OllamaSettings | None = None) -> OllamaStatus:
    settings = settings or get_ollama_settings()
    try:
        version = _get(settings, "/api/version").get("version")
        installed = {m["name"] for m in list_models(settings)}
    except OllamaError as exc:
        return OllamaStatus(False, None, settings.model, False, str(exc))

    available = _full_name(settings.model) in installed
    message = (f"Ollama {version} is running" if available else
               f"Model '{settings.model}' is not downloaded. "
               f"Run: ollama pull {settings.model}")
    return OllamaStatus(True, version, settings.model, available, message)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def _build_payload(settings: OllamaSettings, messages: list[dict], system: str | None,
                   temperature: float | None, response_format: dict | str | None,
                   max_tokens: int | None, stream: bool, model: str | None = None) -> dict:
    if system:
        messages = [{"role": "system", "content": system}, *messages]
    options = {
        "temperature": settings.temperature if temperature is None else temperature,
        "num_ctx": settings.context_size,
    }
    if max_tokens:
        options["num_predict"] = max_tokens      # hard cap on answer length
    payload = {
        "model": model or settings.model,
        "messages": messages,
        "stream": stream,
        "keep_alive": "10m",      # keep the model in memory between questions
        "options": options,
    }
    if response_format is not None:
        payload["format"] = response_format
    return payload


def _post(settings: OllamaSettings, payload: dict, stream: bool) -> requests.Response:
    model = payload["model"]
    try:
        response = requests.post(f"{settings.url}/api/chat", json=payload,
                                 timeout=settings.timeout, stream=stream)
    except requests.ConnectionError as exc:
        raise OllamaError(
            "Ollama is not running. Start the Ollama app (or run 'ollama serve')."
        ) from exc
    except requests.Timeout as exc:
        raise OllamaError(
            f"The model did not answer within {settings.timeout}s. Try again "
            "(the first answer is slow while the model loads), increase "
            "OLLAMA_TIMEOUT, or use a smaller model."
        ) from exc
    if response.status_code == 404:
        raise OllamaError(f"Model '{model}' is not downloaded. "
                          f"Run: ollama pull {model}")
    if not response.ok:
        raise OllamaError(f"Ollama returned an error ({response.status_code}): "
                          f"{response.text[:200]}")
    return response


def chat(messages: list[dict], system: str | None = None,
         temperature: float | None = None,
         response_format: dict | str | None = None,
         max_tokens: int | None = None,
         model: str | None = None,
         settings: OllamaSettings | None = None) -> ChatResult:
    """Send a conversation to the local model and return its whole reply.

    messages:        [{"role": "user" | "assistant", "content": "..."}, ...]
    system:          instructions that shape every answer (the "system prompt")
    response_format: "json", or a JSON schema dict. Ollama then FORCES the
                     answer into that shape ("structured output"), so our
                     code can read it reliably.
    max_tokens:      optional cap on the length of the answer
    model:           use a different installed model for this one call
    """
    settings = settings or get_ollama_settings()
    payload = _build_payload(settings, messages, system, temperature,
                             response_format, max_tokens, stream=False, model=model)
    start = time.perf_counter()
    data = _post(settings, payload, stream=False).json()
    elapsed = time.perf_counter() - start
    result = ChatResult(
        content=data.get("message", {}).get("content", "").strip(),
        model=data.get("model", payload["model"]),
        elapsed_seconds=elapsed,
        load_seconds=data.get("load_duration", 0) / 1e9,     # Ollama reports nanoseconds
        prompt_tokens=data.get("prompt_eval_count", 0),
        output_tokens=data.get("eval_count", 0),
        generation_seconds=data.get("eval_duration", 0) / 1e9,
    )
    # Metadata only: never log the prompt or answer (they may contain data).
    logger.info("LLM answered in %.1fs (%d prompt + %d output tokens, %.1f tok/s)",
                elapsed, result.prompt_tokens, result.output_tokens,
                result.tokens_per_second)
    return result


def ask(prompt: str, system: str | None = None, **kwargs) -> ChatResult:
    """Shortcut for a single question."""
    return chat([{"role": "user", "content": prompt}], system=system, **kwargs)


def chat_stream(messages: list[dict], system: str | None = None,
                temperature: float | None = None,
                max_tokens: int | None = None,
                settings: OllamaSettings | None = None) -> Iterator[str]:
    """Like chat(), but yield the answer in small pieces as the model writes it.

    Ollama then sends one JSON line per piece: {"message": {"content": "The"}, "done": false}
    Total time is the same, but the user starts reading after a second or two
    instead of staring at a spinner.
    """
    settings = settings or get_ollama_settings()
    payload = _build_payload(settings, messages, system, temperature,
                             None, max_tokens, stream=True)
    start = time.perf_counter()
    with _post(settings, payload, stream=True) as response:
        try:
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise OllamaError(f"Ollama error: {data['error']}")
                piece = data.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if data.get("done"):
                    logger.info("LLM streamed answer in %.1fs (%d prompt + %d output tokens)",
                                time.perf_counter() - start,
                                data.get("prompt_eval_count", 0), data.get("eval_count", 0))
                    break
        except requests.RequestException as exc:
            raise OllamaError(f"The connection to Ollama was interrupted: {exc}") from exc
