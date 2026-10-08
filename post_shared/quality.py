"""Shared quality/reliability helpers for the Flora Fauna Jawa bots."""
import json
import os
import random
import time
from typing import Any, Dict, Optional
import requests
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
def request_json(method: str, url: str, *, timeout: int = 20, retries: int = 3, **kwargs) -> Dict[str, Any]:
    last_error = None
    for attempt in range(retries):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in RETRYABLE_STATUS and attempt < retries - 1:
                time.sleep((2 ** attempt) + random.random()); continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries - 1: time.sleep((2 ** attempt) + random.random())
    raise RuntimeError(f"HTTP request gagal setelah {retries} percobaan: {url}") from last_error
def request_bytes(url: str, *, timeout: int = 25, retries: int = 3, headers: Optional[dict] = None) -> bytes:
    last_error = None
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=timeout, headers=headers or {})
            if response.status_code in RETRYABLE_STATUS and attempt < retries - 1:
                time.sleep((2 ** attempt) + random.random()); continue
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries - 1: time.sleep((2 ** attempt) + random.random())
    raise RuntimeError(f"Download gagal setelah {retries} percobaan: {url}") from last_error
def is_image_bytes(data: bytes) -> bool:
    return data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n") or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
def parse_json_object(raw: str) -> Dict[str, Any]:
    data = json.loads(raw.strip())
    if not isinstance(data, dict): raise ValueError("Output AI bukan JSON object.")
    return data
def atomic_write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2); fh.write("\n")
    os.replace(tmp, path)
def validate_infographic_payload(data: Dict[str, Any], expected_latin: str) -> None:
    required = {"category", "iucn_status", "name", "latin_name", "facts", "fb_caption"}
    missing = required - set(data)
    if missing: raise ValueError(f"Output AI kehilangan field: {sorted(missing)}")
    if data["latin_name"].strip().lower() != expected_latin.strip().lower():
        raise ValueError(f"AI mengubah nama latin: expected={expected_latin!r}, got={data['latin_name']!r}")
    if data["category"] not in {"FLORA", "FAUNA"}: raise ValueError(f"Category tidak valid: {data['category']!r}")
    if not isinstance(data["facts"], list) or len(data["facts"]) != 3: raise ValueError("facts harus berisi tepat 3 fakta.")
    for fact in data["facts"]:
        if not isinstance(fact, dict) or not fact.get("title") or not fact.get("desc"): raise ValueError("Setiap fakta wajib memiliki title dan desc.")
    if not data["name"].strip() or not data["iucn_status"].strip() or not data["fb_caption"].strip(): raise ValueError("Nama, status konservasi, dan caption wajib terisi.")
def mask_secret(value: str) -> str:
    if not value: return ""
    if len(value) <= 8: return "***"
    return value[:4] + "..." + value[-4:]
