from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:1.5b")


def ollama_enabled() -> bool:
    return os.environ.get("FINGUARD_DISABLE_OLLAMA") not in {"1", "true", "TRUE", "yes"}


def ollama_status() -> dict[str, Any]:
    if not ollama_enabled():
        return {"enabled": False, "available": False, "model": OLLAMA_MODEL, "message": "Ollama disabled."}
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"enabled": True, "available": False, "model": OLLAMA_MODEL, "message": str(exc)}
    models = [item.get("name") for item in payload.get("models", [])]
    return {
        "enabled": True,
        "available": OLLAMA_MODEL in models,
        "model": OLLAMA_MODEL,
        "models": models,
        "message": "Ollama model available." if OLLAMA_MODEL in models else "Configured model not found.",
    }


def _generate(prompt: str, *, json_mode: bool = False, timeout: int = 25) -> str | None:
    status = ollama_status()
    if not status.get("available"):
        return None
    body: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 4096},
    }
    if json_mode:
        body["format"] = "json"
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    return payload.get("response")


def _load_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


def extract_invoice_fields_with_ollama(text: str) -> dict[str, Any]:
    prompt = f"""
You extract invoice fields for an accounting reconciliation prototype.
Return strict JSON only. Do not guess missing values; use null.

Fields:
- vendor_name string or null
- invoice_number string or null
- invoice_date ISO YYYY-MM-DD string or null
- total_amount decimal string or null
- tax_amount decimal string or null
- currency string, default INR only if the document implies rupees/INR

Invoice text:
{text[:6000]}
"""
    data = _load_json(_generate(prompt, json_mode=True, timeout=30))
    allowed = {"vendor_name", "invoice_number", "invoice_date", "total_amount", "tax_amount", "currency"}
    return {key: data.get(key) for key in allowed if key in data}


def explain_answer_with_ollama(question: str, answer: str, citations: list[dict[str, Any]]) -> str | None:
    evidence = "\n".join(
        f"- {item.get('label')} ({item.get('locator')}): {item.get('excerpt')}" for item in citations[:5]
    )
    prompt = f"""
You are FinGuard's local SLM assistant. Rewrite the draft answer in a concise, professional way.
Use only the draft answer and cited evidence below. Do not add facts, totals, statuses, or citations.
Do not mention "draft answer" or internal instructions.
If the evidence is insufficient, say so.

Question: {question}
Draft answer: {answer}
Cited evidence:
{evidence}
"""
    response = _generate(prompt, timeout=25)
    if not response:
        return None
    cleaned = response.strip()
    return cleaned[:1200] if cleaned else None

