import os
import re
import json
import time
import random
import asyncio
import subprocess
import hashlib
import tempfile
import base64
from datetime import datetime, timezone

import requests
import edge_tts
import imagehash
from PIL import Image
from groq import Groq

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_AUDIO = os.path.join(BASE_DIR, "narasi_en.mp3")
FILE_ASS = os.path.join(BASE_DIR, "narasi_en.ass")
FILE_HOOK_ASS = os.path.join(BASE_DIR, "hook_en.ass")
FILE_FINAL = os.path.join(BASE_DIR, "reels_english.mp4")
FILE_HISTORY = os.path.join(BASE_DIR, "history_reels.json")
FILE_BINTANG = os.path.join(BASE_DIR, "spesies_bintang.json")

# SAFETY: Reels remains disabled until explicitly changed.
# disabled = build nothing beyond validation; dry_run = render/review only; production = publish.
POST_MODE = os.environ.get("POST_MODE", "disabled").strip().lower()
VOICE = "en-US-JennyNeural"
MAX_PHOTOS = 6
MIN_STORY_MEDIA = 4
MIN_STORY_PHOTOS = 3
MIN_SUBJECT_MEDIA = 4
MIN_RELEVANCE_SCORE = 50
PHASH_MAX_DISTANCE = 8
MIN_PIXEL_DISTANCE = 0.075
HARD_DUPLICATE_PHASH_DISTANCE = 4
HARD_DUPLICATE_DHASH_DISTANCE = 4
HARD_DUPLICATE_PIXEL_DISTANCE = 0.025
FINAL_DUPLICATE_PHASH_DISTANCE = 12
FINAL_DUPLICATE_DHASH_DISTANCE = 12
FINAL_DUPLICATE_PIXEL_DISTANCE = 0.12
MIN_VIDEO_SCENE_DISTANCE = 0.08
MAX_MEDIA_CANDIDATES_TO_SCORE = 30
VISION_MODEL = "qwen/qwen3.8-27b"
VISION_POOL_SIZE = 9
VISION_BATCH_SIZE = 3
VISION_MIN_SUBJECT_CONFIDENCE = 0.75
VISION_BATCH_INTERVAL_SECONDS = 61
VISION_MAX_RETRIES = 3
VISION_MIN_STORY_VALUE = 55
VIDEO_MIN_SECONDS = 22.0
VIDEO_MAX_SECONDS = 35.0
FB_API = "https://graph.facebook.com/v21.0"
HEADERS = {"User-Agent": "FloraFaunaJawa/2.1 (+https://github.com/sabuncolek-maker/bot-flora-fauna-jawa)"}
WIKI_HEADERS = {**HEADERS, "Accept": "application/json"}

GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
FB_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if POST_MODE != "disabled" and not GROQ_KEY:
    raise RuntimeError("GROQ_API_KEY belum terpasang.")

def http_json(method, url, retries=3, timeout=30, **kwargs):
    last = None
    for attempt in range(retries):
        try:
            r = requests.request(method, url, timeout=timeout, **kwargs)
            if r.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt < retries - 1:
                time.sleep(2 ** attempt + random.random())
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt + random.random())
    detail = f" ({last})" if last else ""
    raise RuntimeError(f"HTTP gagal setelah {retries} percobaan: {url}{detail}") from last

def wiki_json(params, retries=4, timeout=30):
    return http_json(
        "GET",
        "https://commons.wikimedia.org/w/api.php",
        retries=retries,
        timeout=timeout,
        headers=WIKI_HEADERS,
        params=params,
    )

def http_bytes(url, retries=3, timeout=30):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt < retries - 1:
                time.sleep(2 ** attempt + random.random())
                continue
            r.raise_for_status()
            content_type = (r.headers.get("content-type") or "").lower()
            data = r.content
            valid_magic = (
                data.startswith(b"\xff\xd8\xff") or
                data.startswith(b"\x89PNG\r\n\x1a\n") or
                (data.startswith(b"RIFF") and data[8:12] == b"WEBP") or
                (len(data) > 12 and data[4:8] == b"ftyp") or
                data.startswith(b"\x1aE\xdf\xa3")
            )
            valid_type = (
                content_type.startswith("image/") or
                content_type.startswith("video/") or
                content_type.startswith("application/octet-stream")
            )
            if not valid_magic or not valid_type:
                raise RuntimeError("Response bukan media gambar/video valid.")
            return data
        except (requests.RequestException, RuntimeError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt + random.random())
    detail = f" ({last})" if last else ""
    raise RuntimeError(f"Download media gagal setelah {retries} percobaan: {url}{detail}") from last

def load_history():
    try:
        with open(FILE_HISTORY, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_history_record(species, platform_status):
    history = load_history()
    record = {
        "species": species,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "platforms": platform_status,
    }
    history = [x for x in history if x.get("species") != species] + [record]
    with open(FILE_HISTORY, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
        f.write("\n")

def load_species():
    with open(FILE_BINTANG, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise RuntimeError("spesies_bintang.json kosong/tidak valid.")
    required = {"latin", "indonesia", "fakta_hook", "fakta_singkat", "type"}
    for item in data:
        if not required.issubset(item):
            raise RuntimeError(f"Entry spesies tidak lengkap: {item}")
        if item["type"] not in {"flora", "fauna"}:
            raise RuntimeError(f"Tipe spesies harus flora/fauna: {item}")
    return data

def gbif_occurrence_exists(latin):
    polygon = "POLYGON((105.1 -5.8, 114.6 -5.8, 114.6 -8.8, 105.1 -8.8, 105.1 -5.8))"
    data = http_json("GET", "https://api.gbif.org/v1/occurrence/search", timeout=20,
                     params={"scientificName": latin, "geometry": polygon, "hasCoordinate": "true", "limit": 1})
    return bool(data.get("results"))

def choose_target():
    species = load_species()
    history = load_history()

    complete = {
        x.get("species")
        for x in history
        if isinstance(x, dict)
        and (x.get("platforms") or {}).get("facebook") == "success"
        and (x.get("platforms") or {}).get("instagram") == "success"
    }

    available = [s for s in species if s["latin"] not in complete]
    if not available:
        available = species

    random.shuffle(available)
    for item in available:
        try:
            if gbif_occurrence_exists(item["latin"]):
                print(f"Verified Java occurrence: {item['latin']}")
                return item
        except Exception as exc:
            print(f"GBIF check gagal untuk {item['latin']}: {exc}")
    raise RuntimeError("Tidak ada spesies kurasi dengan occurrence GBIF Jawa yang tervalidasi.")

def get_last_platform_status(species):
    for record in reversed(load_history()):
        if isinstance(record, dict) and record.get("species") == species:
            platforms = record.get("platforms")
            if isinstance(platforms, dict):
                return platforms
    return {}

def _reusable_license(extmetadata):
    raw = " ".join([
        str((extmetadata.get("LicenseShortName") or {}).get("value", "")),
        str((extmetadata.get("UsageTerms") or {}).get("value", "")),
    ]).lower()
    if "public domain" in raw or "cc0" in raw:
        return True
    # Explicitly reject NC/ND licenses. They are not suitable as a generic
    # automated publishing source.
    if re.search(r"\bcc[- ]?by[- ]?(nc|nd)\b", raw):
        return False
    return bool(re.search(r"\bcc[- ]?by(?:[- ]?sa)?(?:\s|[- ]|$)", raw))


def normalize_votes(value):
    """Normalize iNaturalist vote payloads into a sortable numeric score."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("count", "votes", "total"):
            nested = value.get(key)
            if isinstance(nested, (int, float)):
                return nested
            if isinstance(nested, list):
                return len(nested)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def get_media_candidates(latin, common_name):
    candidates = []
    seen_urls = set()
    latin_tokens = {x for x in re.findall(r"[a-z0-9]+", latin.lower()) if len(x) >= 3}
    common_tokens = {x for x in re.findall(r"[a-z0-9]+", common_name.lower()) if len(x) >= 3}
    blocked_visual_terms = {
        "map", "range", "distribution", "illustration", "plate", "diagram",
        "footprint", "footprints", "track", "tracks", "scat", "feces",
        "skull", "skeleton", "bone", "museum", "specimen", "taxidermy"
    }

    def relevance_score(meta):
        text = " ".join(str(meta.get(key, "")) for key in (
            "title", "description", "species_guess", "taxon_name"
        )).lower()
        if any(term in text for term in blocked_visual_terms):
            return -100
        score = 0
        if latin.lower() in text:
            score += 100
        if common_name.lower() in text:
            score += 80
        text_tokens = set(re.findall(r"[a-z0-9]+", text))
        score += 15 * len(latin_tokens.intersection(text_tokens))
        score += 10 * len(common_tokens.intersection(text_tokens))
        return score

    def add(url, kind, source, meta=None):
        if not url or url in seen_urls:
            return
        meta = dict(meta or {})
        lower = url.lower()
        url_text = lower.replace("_", " ")
        if any(token in url_text for token in blocked_visual_terms) or ".svg" in lower:
            return
        meta["relevance"] = relevance_score(meta)
        if meta["relevance"] < 0:
            return
        seen_urls.add(url)
        candidates.append({"url": url, "kind": kind, "source": source, **meta})

    try:
        data = wiki_json(
            params={
                "action": "query", "generator": "search", "gsrsearch": f'"{latin}" OR "{common_name}"',
                "gsrnamespace": 6, "gsrlimit": 100,
                "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                "format": "json", "formatversion": 2,
            }, timeout=25,
        )
        for page in data.get("query", {}).get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            mime = (info.get("mime") or "").lower()
            ext = info.get("extmetadata") or {}
            if mime.startswith("video/") and _reusable_license(ext):
                add(info.get("url"), "video", "Wikimedia Commons", {
                    "title": page.get("title"),
                    "license": (ext.get("LicenseShortName") or {}).get("value", "unknown"),
                    "mime": mime,
                })
    except Exception as exc:
        print(f"Wikimedia video search gagal: {exc}")

    try:
        for page in range(1, 5):
            data = http_json(
                "GET", "https://api.inaturalist.org/v1/observations",
                params={
                    "taxon_name": latin, "has[]": "photos", "quality_grade": "research",
                    "order_by": "votes", "order": "desc",
                    "per_page": 100, "page": page,
                }, timeout=20,
            )
            results = data.get("results", [])
            for obs in results:
                for photo in obs.get("photos", []):
                    license_code = (photo.get("license_code") or "").lower()
                    if license_code not in {"cc0", "cc-by", "cc-by-sa"}:
                        continue
                    url = (photo.get("url") or "").replace("/square.", "/large.")
                    add(url, "photo", "iNaturalist", {
                        "license": license_code or "license-not-returned",
                        "observation_id": obs.get("id"),
                        "description": obs.get("description") or "",
                        "species_guess": obs.get("species_guess") or "",
                        "taxon_name": ((obs.get("taxon") or {}).get("name") or ""),
                        "votes": normalize_votes(obs.get("votes")),
                    })
            if not results:
                break
    except Exception as exc:
        print(f"iNaturalist gagal: {exc}")

    try:
        data = wiki_json(
            params={
                "action": "query", "generator": "search", "gsrsearch": latin,
                "gsrnamespace": 6, "gsrlimit": 100,
                "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                "iiurlwidth": 1600, "format": "json", "formatversion": 2,
            }, timeout=25,
        )
        for page in data.get("query", {}).get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            mime = (info.get("mime") or "").lower()
            ext = info.get("extmetadata") or {}
            if not mime.startswith("image/") or mime == "image/svg+xml" or not _reusable_license(ext):
                continue
            add(info.get("thumburl") or info.get("url"), "photo", "Wikimedia Commons", {
                "title": page.get("title"),
                "license": (ext.get("LicenseShortName") or {}).get("value", "unknown"),
            })
    except Exception as exc:
        print(f"Wikimedia photo search gagal: {exc}")

    # Wikipedia article images are not used as an automated media source.
    # The article license does not prove that the individual image is reusable.
    return candidates


def _image_visual_profile(image):
    """Build lightweight visual fingerprints for composition-level diversity."""
    image = image.convert("RGB")
    phash = imagehash.phash(image)
    dhash = imagehash.dhash(image)
    gray = image.convert("L").resize((32, 32))
    pixels = list(gray.get_flattened_data())
    return {
        "phash": phash,
        "dhash": dhash,
        "pixels": pixels,
        "width": image.width,
        "height": image.height,
    }


def _pixel_distance(first, second):
    if not first or not second:
        return 1.0
    return sum(abs(a - b) for a, b in zip(first, second)) / (255.0 * len(first))


def _visual_distance(first, second):
    return {
        "phash": first["phash"] - second["phash"],
        "dhash": first["dhash"] - second["dhash"],
        "pixels": _pixel_distance(first["pixels"], second["pixels"]),
    }


def _profile_from_file(path):
    with Image.open(path) as image:
        return _image_visual_profile(image)


def _video_visual_profiles(path):
    profiles = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for index, position in enumerate(("0.15", "0.50", "0.85")):
            frame_path = os.path.join(tmpdir, f"frame_{index}.jpg")
            probe = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", position, "-i", path,
                    "-frames:v", "1", "-q:v", "3", frame_path
                ],
                capture_output=True, timeout=20
            )
            if probe.returncode != 0 or not os.path.exists(frame_path):
                continue
            try:
                profiles.append(_profile_from_file(frame_path))
            except Exception:
                continue
    return profiles


def visual_profiles_for_media(path, kind):
    if kind == "photo":
        profile = _profile_from_file(path)
        return [profile], profile
    profiles = _video_visual_profiles(path)
    if not profiles:
        return [], None
    return profiles, profiles[len(profiles) // 2]


def video_scene_diversity(profiles):
    if len(profiles) < 2:
        return 0.0
    distances = []
    for index, first in enumerate(profiles):
        for second in profiles[index + 1:]:
            distances.append(_visual_distance(first, second)["pixels"])
    return max(distances) if distances else 0.0


def is_visually_duplicate(candidate_profile, accepted_profiles):
    """Reject only obvious near-identical media before semantic Vision review."""
    if candidate_profile is None:
        return False

    for accepted in accepted_profiles:
        distance = _visual_distance(candidate_profile, accepted)

        # Classical CV is only an anti-duplicate filter here.
        # Similar-but-editorially-distinct shots must reach Groq Vision.
        hard_duplicate = (
            distance["phash"] <= HARD_DUPLICATE_PHASH_DISTANCE
            and distance["dhash"] <= HARD_DUPLICATE_DHASH_DISTANCE
            and distance["pixels"] <= HARD_DUPLICATE_PIXEL_DISTANCE
        )
        if hard_duplicate:
            return True

    return False


def _media_quality_score(candidate, profiles, representative):
    if representative is None:
        return -100.0
    width = representative.get("width", 0)
    height = representative.get("height", 0)
    resolution_score = min(20.0, (width * height) / 250000.0)
    contrast = 0.0
    pixels = representative.get("pixels") or []
    if pixels:
        mean = sum(pixels) / len(pixels)
        variance = sum((value - mean) ** 2 for value in pixels) / len(pixels)
        contrast = min(15.0, (variance ** 0.5) / 8.0)
    source_bonus = 10.0 if candidate["source"] == "iNaturalist" else 5.0
    motion_bonus = min(15.0, video_scene_diversity(profiles) * 120.0) if candidate["kind"] == "video" else 0.0
    return resolution_score + contrast + source_bonus + motion_bonus


def _story_novelty_score(representative, accepted_profiles):
    if representative is None or not accepted_profiles:
        return 100.0
    distances = [_visual_distance(representative, previous) for previous in accepted_profiles]
    return min(
        100.0,
        max(
            distance["phash"] * 2.0
            for distance in distances
        )
        + max(distance["pixels"] for distance in distances) * 100.0,
    )


def _vision_encode_image(path, kind):
    """Encode a local photo or a video contact sheet for Groq Vision."""
    if kind == "photo":
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((1280, 1280))
            with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
                image.save(tmp.name, "JPEG", quality=82, optimize=True)
                with open(tmp.name, "rb") as image_file:
                    payload = base64.b64encode(image_file.read()).decode("utf-8")
        return f"data:image/jpeg;base64,{payload}"

    with tempfile.TemporaryDirectory() as tmpdir:
        frame_paths = []
        for index, position in enumerate(("0.15", "0.50", "0.85")):
            frame_path = os.path.join(tmpdir, f"frame_{index}.jpg")
            probe = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", position, "-i", path,
                    "-frames:v", "1", "-vf", "scale=480:-2",
                    "-q:v", "4", frame_path
                ],
                capture_output=True, timeout=20
            )
            if probe.returncode == 0 and os.path.exists(frame_path):
                frame_paths.append(frame_path)

        if not frame_paths:
            raise RuntimeError("Video tidak menghasilkan frame untuk Vision.")

        frames = []
        for frame_path in frame_paths:
            with Image.open(frame_path) as image:
                frames.append(image.convert("RGB"))

        width = 480 * len(frames)
        height = max(image.height for image in frames)
        sheet = Image.new("RGB", (width, height), "white")
        x = 0
        for image in frames:
            sheet.paste(image, (x, 0))
            x += image.width

        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            sheet.save(tmp.name, "JPEG", quality=82, optimize=True)
            with open(tmp.name, "rb") as image_file:
                payload = base64.b64encode(image_file.read()).decode("utf-8")
        return f"data:image/jpeg;base64,{payload}"


def _vision_analyze_batch(batch, latin, common_name):
    client = Groq(api_key=GROQ_KEY)
    content = [{
        "type": "text",
        "text": (
            f"You are the visual editor for a wildlife Reel about {common_name} "
            f"({latin}). Inspect each image independently. Do not trust filenames or "
            f"metadata as proof that the subject is visible. Reject images showing only "
            f"tracks, footprints, habitat, maps, diagrams, specimens, objects, or scenery "
            f"when the species itself is not clearly visible. For videos, each image is a "
            f"contact sheet sampled from the same video. Evaluate each image as an editorial "
            f"Reel asset, not as a dramatic wildlife event. A still image can have high story value "
            f"even when the subject is resting or not behaving dramatically. Story value measures "
            f"usefulness in telling a clear visual story about the species. "
            f"Use this story_value rubric: 90-100 exceptional and distinctive; 75-89 strong and "
            f"useful; 55-74 acceptable Reel visual when the subject is clearly visible, relevant, "
            f"reasonably clear, and contributes a useful visual beat; 30-54 weak or repetitive; "
            f"0-29 unusable. Do not give a low story_value merely because the subject is stationary, "
            f"common-looking, or lacks dramatic behavior. Judge clarity, relevance, composition, "
            f"shot type, visual quality, and contribution to the sequence. Use the full 0-100 range "
            f"consistently. Return one result per image in the same order. "
            f"Use confidence values from 0 to 1 and scores from 0 to 100."
        )
    }]

    for index, item in enumerate(batch, 1):
        content.append({
            "type": "text",
            "text": f"IMAGE {index}: {item['kind']} | source={item.get('source', '')}"
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": _vision_encode_image(item["path"], item["kind"])}
        })

    request_kwargs = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "max_completion_tokens": 1200,
        "reasoning_effort": "none",
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "visual_editor_batch",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "subject_visible": {"type": "boolean"},
                                    "subject_confidence": {"type": "number"},
                                    "shot_type": {
                                        "type": "string",
                                        "enum": ["close_up", "medium", "wide", "detail", "unknown"]
                                    },
                                    "composition": {
                                        "type": "string",
                                        "enum": ["subject_dominant", "balanced", "background_dominant", "unknown"]
                                    },
                                    "story_value": {"type": "number"},
                                    "visual_quality": {"type": "number"},
                                    "behavior": {"type": "string"},
                                    "reject_reason": {"type": "string"},
                                },
                                "required": [
                                    "subject_visible", "subject_confidence", "shot_type",
                                    "composition", "story_value", "visual_quality",
                                    "behavior", "reject_reason"
                                ],
                                "additionalProperties": False,
                            }
                        }
                    },
                    "required": ["results"],
                    "additionalProperties": False,
                }
            }
        }
    }

    last_error = None
    for attempt in range(VISION_MAX_RETRIES):
        try:
            response = client.chat.completions.create(**request_kwargs)
            break
        except Exception as exc:
            last_error = exc
            status_code = getattr(exc, "status_code", None)
            if status_code == 429:
                delay = 61
            elif status_code in {408, 425, 500, 502, 503, 504}:
                delay = 2 ** attempt + random.random()
            else:
                raise
            if attempt >= VISION_MAX_RETRIES - 1:
                raise RuntimeError(
                    f"Groq Vision gagal setelah {VISION_MAX_RETRIES} percobaan "
                    f"(status={status_code}): {exc}"
                ) from exc
            print(
                f"Groq Vision retry {attempt + 1}/{VISION_MAX_RETRIES} "
                f"setelah status={status_code}; tunggu {delay:.0f}s."
            )
            time.sleep(delay)
    else:
        raise RuntimeError(f"Groq Vision gagal: {last_error}") from last_error

    payload = json.loads(response.choices[0].message.content or "{}")
    results = payload.get("results") or []
    if len(results) != len(batch):
        raise RuntimeError(
            f"Vision mengembalikan {len(results)} hasil untuk {len(batch)} gambar."
        )
    return results


def apply_vision_editor(media_items, latin, common_name):
    """Use Groq Vision as the final semantic gate and visual-story scorer."""
    if not GROQ_KEY:
        raise RuntimeError("GROQ_API_KEY diperlukan untuk Visual Editor.")

    for batch_index, start in enumerate(range(0, len(media_items), VISION_BATCH_SIZE)):
        if batch_index:
            print(
                f"Vision rate-limit guard: waiting {VISION_BATCH_INTERVAL_SECONDS}s "
                f"before batch {batch_index + 1}."
            )
            time.sleep(VISION_BATCH_INTERVAL_SECONDS)
        batch = media_items[start:start + VISION_BATCH_SIZE]
        results = _vision_analyze_batch(batch, latin, common_name)
        for item, result in zip(batch, results):
            item["vision"] = result

    approved = []
    for item in media_items:
        vision = item.get("vision") or {}
        confidence = float(vision.get("subject_confidence", 0))
        story_value = float(vision.get("story_value", 0))
        visible = bool(vision.get("subject_visible"))
        if not visible or confidence < VISION_MIN_SUBJECT_CONFIDENCE:
            print(
                f"Vision reject: subject_visible={visible} confidence={confidence:.2f} | "
                f"{item.get('source')} | {item.get('path')}"
            )
            continue
        if story_value < VISION_MIN_STORY_VALUE:
            print(
                f"Vision reject: story_value={story_value:.1f} | "
                f"{item.get('source')} | {item.get('path')}"
            )
            continue

        item["vision_score"] = round(
            confidence * 45.0
            + story_value * 0.35
            + float(vision.get("visual_quality", 0)) * 0.20,
            2,
        )
        approved.append(item)

    if len(approved) < MIN_STORY_MEDIA:
        raise RuntimeError(
            f"Vision story gate gagal: hanya {len(approved)}/{MIN_STORY_MEDIA} "
            f"media lolos semantic visual verification untuk {latin}."
        )

    # Final visual verification: Vision-approved files must still be
    # materially distinct before entering the renderer.
    selected = []
    selected_profiles = []
    selected_hashes = set()
    used_shot_types = set()

    ranked = sorted(
        approved,
        key=lambda x: (
            x.get("vision_score", 0),
            x.get("visual_novelty", 0),
            x.get("relevance", 0),
        ),
        reverse=True,
    )

    def final_duplicate(item):
        digest = item.get("sha256")
        if digest and digest in selected_hashes:
            return True
        try:
            profile = _profile_from_file(item["path"])
        except Exception as exc:
            print(f"Final visual audit gagal membaca media: {item.get('path')} | {exc}")
            return True

        for previous in selected_profiles:
            distance = _visual_distance(profile, previous)
            signals = (
                distance["phash"] <= FINAL_DUPLICATE_PHASH_DISTANCE,
                distance["dhash"] <= FINAL_DUPLICATE_DHASH_DISTANCE,
                distance["pixels"] <= FINAL_DUPLICATE_PIXEL_DISTANCE,
            )
            if sum(signals) >= 2:
                print(
                    f"Final visual duplicate: {item.get('path')} | "
                    f"phash={distance['phash']} dhash={distance['dhash']} "
                    f"pixels={distance['pixels']:.3f}"
                )
                return True
        return False

    for item in ranked:
        if final_duplicate(item):
            continue
        try:
            profile = _profile_from_file(item["path"])
        except Exception:
            continue
        shot_type = (item.get("vision") or {}).get("shot_type", "unknown")
        diversity_bonus = 1 if shot_type not in used_shot_types else 0
        item["_selection_score"] = item.get("vision_score", 0) + diversity_bonus * 8
        selected.append(item)
        selected_profiles.append(profile)
        if item.get("sha256"):
            selected_hashes.add(item["sha256"])
        used_shot_types.add(shot_type)
        if len(selected) >= MAX_PHOTOS:
            break

    print(
        f"Final visual audit: {len(selected)}/{len(approved)} unique visual assets "
        f"after SHA-256 + perceptual verification."
    )

    if len(selected) < MIN_STORY_MEDIA:
        raise RuntimeError(
            f"Final visual diversity gate gagal: hanya {len(selected)}/{MIN_STORY_MEDIA} "
            f"visual berbeda setelah final verification untuk {latin}."
        )

    if sum(x["kind"] == "photo" for x in selected) < MIN_STORY_PHOTOS:
        photo_candidates = [x for x in approved if x["kind"] == "photo" and x not in selected]
        photo_candidates.sort(key=lambda x: x.get("vision_score", 0), reverse=True)
        for replacement in photo_candidates:
            photos = [x for x in selected if x["kind"] == "photo"]
            videos = [x for x in selected if x["kind"] == "video"]
            if len(photos) >= MIN_STORY_PHOTOS:
                break
            if videos:
                selected = photos + [replacement] + videos[:1]
            else:
                selected.append(replacement)
            selected = selected[:MAX_PHOTOS]

    if sum(x["kind"] == "photo" for x in selected) < MIN_STORY_PHOTOS:
        raise RuntimeError(
            f"Vision story gate gagal: hanya "
            f"{sum(x['kind'] == 'photo' for x in selected)}/{MIN_STORY_PHOTOS} foto lolos."
        )

    for item in selected:
        item.pop("_selection_score", None)

    return selected


def download_species_media(latin, common_name):
    candidates = get_media_candidates(latin, common_name)
    saved = []
    hashes = set()
    accepted_profiles = []
    metadata = []

    candidates.sort(
        key=lambda x: (
            x.get("relevance", 0),
            normalize_votes(x.get("votes", 0)),
            1 if x["source"] == "iNaturalist" else 0,
        ),
        reverse=True,
    )

    def accept_candidate(candidate, data, path):
        digest = hashlib.sha256(data).hexdigest()
        if digest in hashes:
            return False

        try:
            profiles, representative = visual_profiles_for_media(path, candidate["kind"])
        except Exception as exc:
            print(f"Media ditolak: visual analysis gagal | {candidate['source']} | {exc}")
            return False

        if representative is None:
            return False

        if candidate["kind"] == "video":
            scene_distance = video_scene_diversity(profiles)
            if scene_distance < MIN_VIDEO_SCENE_DISTANCE:
                print(
                    f"Video ditolak: scene terlalu monoton ({scene_distance:.3f}) | "
                    f"{candidate['source']} | {candidate.get('title', '')}"
                )
                return False

        if is_visually_duplicate(representative, accepted_profiles):
            print(
                f"Media ditolak: visual terlalu mirip dengan shot sebelumnya | "
                f"{candidate['source']} | {candidate.get('title', '')}"
            )
            return False

        hashes.add(digest)
        accepted_profiles.append(representative)
        saved.append({"path": path, "kind": candidate["kind"]})
        metadata.append({
            **candidate,
            "path": path,
            "sha256": digest,
            "phash": str(representative["phash"]),
            "dhash": str(representative["dhash"]),
            "visual_novelty": round(_story_novelty_score(representative, accepted_profiles[:-1]), 2),
            "visual_quality": round(_media_quality_score(candidate, profiles, representative), 2),
            "scene_diversity": round(video_scene_diversity(profiles), 3),
        })
        return True

    subject_relevant_count = 0
    photo_count = 0

    for candidate in candidates[:MAX_MEDIA_CANDIDATES_TO_SCORE]:
        if candidate.get("relevance", 0) < MIN_RELEVANCE_SCORE:
            continue
        if candidate["kind"] != "video":
            continue
        if sum(x["kind"] == "video" for x in saved) >= 1:
            continue
        try:
            data = http_bytes(candidate["url"])
            mime = (candidate.get("mime") or "").lower()
            ext = ".webm" if "webm" in mime else ".ogv" if "ogg" in mime else ".mp4"
            path = os.path.join(BASE_DIR, f"media_candidate_{len(saved) + 1}{ext}")
            with open(path, "wb") as f:
                f.write(data)
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=codec_type,duration,width,height",
                    "-of", "json", path
                ],
                capture_output=True, text=True, timeout=15
            )
            if probe.returncode != 0 or not probe.stdout.strip():
                os.remove(path)
                continue
            if accept_candidate(candidate, data, path):
                subject_relevant_count += 1
                print(
                    f"Video story candidate diterima | relevance={candidate.get('relevance', 0)} | "
                    f"scene={metadata[-1]['scene_diversity']:.3f}"
                )
            else:
                if os.path.exists(path):
                    os.remove(path)
        except Exception as exc:
            print(f"Video ditolak: {candidate['url']} ({exc})")

    for candidate in candidates[:MAX_MEDIA_CANDIDATES_TO_SCORE]:
        if candidate.get("relevance", 0) < MIN_RELEVANCE_SCORE:
            continue
        if candidate["kind"] != "photo" or len(saved) >= VISION_POOL_SIZE:
            continue
        try:
            data = http_bytes(candidate["url"])
            path = os.path.join(BASE_DIR, f"media_candidate_{len(saved) + 1}.jpg")
            with open(path, "wb") as f:
                f.write(data)
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height", "-of", "csv=p=0", path
                ],
                capture_output=True, text=True, timeout=10
            )
            if probe.returncode != 0 or not probe.stdout.strip():
                os.remove(path)
                continue
            if accept_candidate(candidate, data, path):
                subject_relevant_count += 1
                photo_count += 1
                print(
                    f"Foto story candidate diterima: {photo_count} | "
                    f"relevance={candidate.get('relevance', 0)} | "
                    f"novelty={metadata[-1]['visual_novelty']:.1f}"
                )
            else:
                if os.path.exists(path):
                    os.remove(path)
        except Exception as exc:
            print(f"Foto ditolak: {candidate['url']} ({exc})")

    if len(saved) < MIN_STORY_MEDIA:
        raise RuntimeError(
            f"Story media gagal: hanya {len(saved)}/{MIN_STORY_MEDIA} visual berbeda "
            f"yang lolos visual diversity gate untuk {latin}."
        )

    if photo_count < MIN_STORY_PHOTOS:
        raise RuntimeError(
            f"Story media gagal: hanya {photo_count}/{MIN_STORY_PHOTOS} foto berbeda "
            f"yang lolos visual diversity gate untuk {latin}."
        )

    if subject_relevant_count < MIN_SUBJECT_MEDIA:
        raise RuntimeError(
            f"Media gagal evidence gate untuk {latin}: hanya {subject_relevant_count}/"
            f"{MIN_SUBJECT_MEDIA} media relevan yang lolos."
        )

    by_path = {x["path"]: x for x in metadata}
    vision_items = [by_path[x["path"]] for x in saved]
    selected_metadata = apply_vision_editor(vision_items, latin, common_name)

    selected_paths = {x["path"] for x in selected_metadata}
    for item in saved:
        if item["path"] not in selected_paths and os.path.exists(item["path"]):
            os.remove(item["path"])

    selected = [
        {"path": item["path"], "kind": item["kind"]}
        for item in selected_metadata
    ]

    # Story sequence: Vision score first, then semantic shot diversity.
    selected.sort(
        key=lambda x: (
            by_path.get(x["path"], {}).get("vision_score", 0),
            by_path.get(x["path"], {}).get("visual_novelty", 0),
            by_path.get(x["path"], {}).get("relevance", 0),
        ),
        reverse=True,
    )

    photos = [x for x in selected if x["kind"] == "photo"]
    videos = [x for x in selected if x["kind"] == "video"]
    saved = (photos[:3] + videos[:1] + photos[3:])[:MAX_PHOTOS]

    with open(os.path.join(BASE_DIR, "media_sources.json"), "w", encoding="utf-8") as f:
        json.dump(
            [by_path.get(x["path"], x) for x in saved],
            f, ensure_ascii=False, indent=2
        )

    print(
        "FINAL MEDIA SELECTION: "
        + " | ".join(
            f"{index + 1}. {os.path.basename(item['path'])} "
            f"sha256={item.get('sha256', '')[:12]} "
            f"shot={(item.get('vision') or {}).get('shot_type', 'unknown')}"
            for index, item in enumerate(saved)
        )
    )
    print(
        f"Vision story QA OK: {len(saved)} visual; "
        f"photos={sum(x['kind'] == 'photo' for x in saved)}; "
        f"videos={sum(x['kind'] == 'video' for x in saved)}; "
        f"vision_model={VISION_MODEL}"
    )
    return saved


def choose_content_format(item):
    fact = item["fakta_singkat"].lower()
    if item["type"] == "flora":
        formats = ["plant_mystery", "one_fact", "detective", "java_file"]
    else:
        formats = ["guess", "one_fact", "detective", "myth_fact", "java_file"]
        if any(x in fact for x in ["infant", "born", "young", "baby"]):
            formats.append("baby_adult")
        if "threatened" in fact or "endangered" in fact:
            formats.append("threatened")
    return random.choice(formats)


def generate_english_script(item, content_format):
    fact = re.sub(r"\s+", " ", str(item.get("fakta_singkat", "")).strip()).strip()
    species = item["latin"]
    common_name = item["indonesia"]
    subject = "animal" if item["type"] == "fauna" else "plant"

    def fallback():
        # Deterministic fallback must always pass narration QA even when Groq/API
        # is unavailable. Keep it factual and long enough for the TTS window.
        templates = {
            "plant_mystery": (
                f"Can you identify this plant? It is {common_name}, known scientifically as {species}. "
                f"Here is the clue: {fact} This is one of Java's remarkable plants."
            ),
            "guess": (
                f"Can you identify this animal? It is {common_name}, known scientifically as {species}. "
                f"Here is the clue: {fact} This is one of the remarkable species found in Java."
            ),
            "one_fact": (
                f"Here is one fact worth remembering about {common_name}. {fact} "
                f"This species is part of Java's remarkable biodiversity."
            ),
            "detective": (
                f"Case file: can you identify the species from the evidence? "
                f"The answer is {common_name}, scientifically known as {species}. {fact} "
                f"Another fascinating story from Java's biodiversity."
            ),
            "myth_fact": (
                f"Myth or fact? {fact} The answer is fact, based on the supplied species information. "
                f"This is {common_name}, a species associated with Java's biodiversity."
            ),
            "baby_adult": (
                f"Look closely at the young and adult stages of {common_name}. {fact} "
                f"These visual details can help us recognize this species in Java."
            ),
            "threatened": (
                f"This is {common_name}, scientifically known as {species}. {fact} "
                f"Its story is part of Java's important biodiversity heritage."
            ),
            "java_file": (
                f"Java biodiversity file: {common_name}, scientifically known as {species}. {fact} "
                f"One more remarkable species from the biodiversity of Java."
            ),
        }
        return re.sub(r"\s+", " ", templates.get(content_format, templates["java_file"])).strip()

    try:
        client = Groq(api_key=GROQ_KEY)
        instructions = {
            "plant_mystery": "Build a curiosity-first plant identification puzzle. Do not call the plant an animal.",
            "guess": "Build a curiosity-first animal identification puzzle. Do not reveal the answer until near the end.",
            "one_fact": "Open immediately with the single most surprising fact. No generic introduction.",
            "detective": "Write it like a mini wildlife investigation with two or three clues, then reveal the species.",
            "myth_fact": "Start with 'Myth or fact?' Present the supplied fact and clearly reveal the verdict.",
            "baby_adult": "Focus on the visual difference between young and adult stages, using only the supplied fact.",
            "threatened": "Use a serious documentary tone and emphasize the supplied threatened-status fact without exaggerating.",
            "java_file": "Make it feel like a fast 20-second wildlife case file about Java.",
        }
        prompt = f"""Write a high-retention English narration for a short vertical wildlife Reel about {species} ({common_name}) from Java, Indonesia.

SUBJECT TYPE: {subject}
FORMAT: {content_format}
{instructions.get(content_format, instructions["java_file"])}

ONLY use this editorial fact as factual information:
- {fact}

Do not invent population numbers, exact locations, measurements, behavior, causes, conservation status, or comparisons.
Do not use unsupported superlatives.
Write 38-58 words. Keep sentences short and easy to subtitle.
Start with a strong curiosity hook. End with the species name or a memorable final line when appropriate.
Tone: cinematic, curious, intelligent, documentary-style.
Return only narration. No title, bullets, markdown, URLs, hashtags, or citations."""
        for attempt in range(3):
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=120,
            )
            raw = response.choices[0].message.content if response.choices else ""
            text = re.sub(r"\s+", " ", (raw or "").strip()).strip()
            if 36 <= len(text.split()) <= 65:
                return text
    except Exception as exc:
        print(f"Groq narration unavailable, using deterministic fallback: {exc}")

    text = fallback()
    # Deterministic fallback must be long enough for the TTS duration floor.
    # Add only non-factual editorial pacing lines; never invent species facts.
    pacing = [
        "Keep watching closely, because the visual details are the clue.",
        "This quick field note comes from Java's remarkable biodiversity.",
    ]
    while len(text.split()) < 42 and pacing:
        text = f"{text} {pacing.pop(0)}"
    if 36 <= len(text.split()) <= 70:
        print(f"Using deterministic English narration fallback ({len(text.split())} words).")
        return text
    raise RuntimeError("English narration fallback failed QA.")


def generate_english_hook(item, content_format):
    if item["type"] == "flora":
        hooks = {
            "plant_mystery": "CAN YOU IDENTIFY THIS PLANT?",
            "one_fact": item.get("fakta_hook", "ONE WILD FACT"),
            "detective": "BOTANICAL DETECTIVE",
            "java_file": "JAVA PLANT FILE",
        }
        return hooks.get(content_format, "JAVA PLANT")
    hooks = {
        "guess": "CAN YOU IDENTIFY THIS ANIMAL?",
        "one_fact": item.get("fakta_hook", "ONE WILD FACT"),
        "detective": "WILDLIFE DETECTIVE",
        "myth_fact": "MYTH OR FACT?",
        "baby_adult": "BABY VS ADULT",
        "threatened": "THREATENED WILDLIFE",
        "java_file": "JAVA WILDLIFE FILE",
    }
    return hooks.get(content_format, "JAVA WILDLIFE")


def segment_script(text):
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
    segments = []
    for sentence in sentences:
        words = sentence.split()
        for i in range(0, len(words), 7):
            chunk = words[i:i+7]
            if chunk:
                segments.append(" ".join(chunk))
    if not segments:
        raise RuntimeError("Subtitle segmentation kosong.")
    return segments

def norm_word(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())

async def make_tts_and_subtitles(script, segments):
    current_script = script
    last_duration = None

    for attempt in range(3):
        current_segments = segment_script(current_script)
        tts_text = " ".join(current_segments)
        communicator = edge_tts.Communicate(tts_text, VOICE, rate="-12%", pitch="-2Hz")
        audio_chunks, bounds = [], []

        try:
            async for chunk in communicator.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word = norm_word(chunk.get("text", ""))
                    if word:
                        start = chunk["offset"] / 10_000_000
                        end = start + chunk["duration"] / 10_000_000
                        bounds.append((word, start, end))
        except Exception as exc:
            raise RuntimeError(f"English TTS gagal: {exc}") from exc

        if not audio_chunks:
            raise RuntimeError("English TTS tidak menghasilkan audio.")

        with open(FILE_AUDIO, "wb") as f:
            for chunk in audio_chunks:
                f.write(chunk)

        audio_duration = probe_duration(FILE_AUDIO)
        duration = audio_duration + 3.0
        last_duration = duration

        if VIDEO_MIN_SECONDS <= duration <= VIDEO_MAX_SECONDS:
            segment_times = []

            if bounds:
                cursor = 0
                mapping_failed = False
                for segment in current_segments:
                    words = [norm_word(w) for w in segment.split() if norm_word(w)]
                    starts, ends = [], []
                    for word in words:
                        found = None
                        for j in range(cursor, len(bounds)):
                            if bounds[j][0] == word:
                                found = j
                                break
                        if found is None:
                            mapping_failed = True
                            break
                        starts.append(bounds[found][1])
                        ends.append(bounds[found][2])
                        cursor = found + 1
                    if mapping_failed or not starts:
                        break
                    segment_times.append((starts[0], ends[-1]))

                if mapping_failed or len(segment_times) != len(current_segments):
                    segment_times = []

            if not segment_times:
                weights = [max(1, len([w for w in seg.split() if norm_word(w)])) for seg in current_segments]
                total_weight = sum(weights)
                cursor = 0.0
                for weight in weights:
                    span = audio_duration * weight / total_weight
                    segment_times.append((cursor, cursor + span))
                    cursor += span

            offset = 3.0
            shifted = [(a + offset, b + offset) for a, b in segment_times]

            with open(FILE_ASS, "w", encoding="utf-8") as f:
                f.write("""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Outline, Alignment, MarginV
Style: Narasi,DejaVu Sans,44,&H00FFFFFF,&H00000000,1,3,2,210

[Events]
Format: Layer, Start, End, Style, Text
""")
                for segment, (start, end) in zip(current_segments, shifted):
                    safe = segment.replace("{", "").replace("}", "")
                    f.write(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Narasi,{safe.upper()}\n")

            print(f"TTS QA OK: {duration:.1f}s on attempt {attempt + 1}.")
            return duration, current_script

        print(f"TTS duration {duration:.1f}s is outside {VIDEO_MIN_SECONDS}-{VIDEO_MAX_SECONDS}s.")
        if duration < VIDEO_MIN_SECONDS:
            words = current_script.split()
            # Target ~24s, leaving headroom below the 35s ceiling.
            target_words = min(65, max(len(words) + 8, int(len(words) * 1.28)))
            padding = [
                "Keep watching closely, because the visual details are the clue.",
                "This quick field note comes from Java's remarkable biodiversity.",
            ]
            while len(current_script.split()) < target_words and padding:
                current_script = f"{current_script} {padding.pop(0)}"
        elif duration > VIDEO_MAX_SECONDS:
            words = current_script.split()
            target_words = max(38, int(len(words) * 0.84))
            if len(words) <= target_words:
                break
            current_script = " ".join(words[:target_words]).rstrip(" ,.;:") + "."
        else:
            current_script = current_script

    raise RuntimeError(f"TTS duration QA failed after 3 attempts; last duration={last_duration:.1f}s.")

def ass_time(seconds):
    cs = int((seconds - int(seconds)) * 100)
    total = int(seconds)
    return f"{total // 3600}:{(total // 60) % 60:02d}:{total % 60:02d}.{cs:02d}"

def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("ffprobe gagal.")
    return float(r.stdout.strip())

def render_reel(media, hook, duration):
    weights = []
    for i, item in enumerate(media):
        weight = 1.18 if item["kind"] == "video" else 0.92
        if i == 0:
            weight *= 1.10
        weights.append(weight)
    total_weight = sum(weights)
    media_durations = [duration * w / total_weight for w in weights]

    cmd = ["ffmpeg", "-y"]
    filters = []
    fps = 25

    for i, item in enumerate(media):
        path = item["path"]
        kind = item["kind"]
        cmd += ["-stream_loop", "-1"] if kind == "video" else ["-loop", "1"]
        cmd += ["-t", f"{media_durations[i]:.3f}", "-i", path]
        frames = max(1, round(media_durations[i] * fps))
        motion = "1+0.00045*on" if i % 2 == 0 else "max(1.08-0.00045*on\\,1.0)"

        if kind == "video":
            filters.append(
                f"[{i}:v]fps={fps},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"boxblur=15:3[bg{i}];"
                f"[{i}:v]fps={fps},scale=1080:1920:force_original_aspect_ratio=decrease[fg{i}];"
                f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1[v{i}]"
            )
        else:
            filters.append(
                f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"boxblur=15:3[bg{i}];"
                f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg{i}];"
                f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,scale=2160:3840,"
                f"zoompan=z='{motion}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s=1080x1920:fps={fps},setsar=1[v{i}]"
            )

    labels = "".join(f"[v{i}]" for i in range(len(media)))
    filters.append(
        f"{labels}concat=n={len(media)}:v=1:a=0,"
        f"fps={fps},format=yuv420p,setpts=PTS-STARTPTS[vbase]"
    )

    hook_clean = re.sub(r"[^A-Za-z0-9' !?-]", "", hook).upper()
    with open(FILE_HOOK_ASS, "w", encoding="utf-8") as f:
        f.write("""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Outline, Alignment, MarginV
Style: Hook,DejaVu Sans,78,&H00FFFFFF,&H00000000,1,5,5,0
[Events]
Format: Layer, Start, End, Style, Text
""")
        f.write(f"Dialogue: 0,0:00:00.50,0:00:03.00,Hook,{hook_clean}\\n")

    idx_audio = len(media)
    cmd += ["-i", FILE_AUDIO]
    filters.append("[vbase]ass=hook_en.ass,ass=narasi_en.ass[vout]")
    filters.append("anoisesrc=color=brown:duration=40:sample_rate=44100[noise];[noise]lowpass=f=400,volume=0.06[amb]")
    filters.append(f"[{idx_audio}:a]adelay=3000|3000[narr];[narr][amb]amix=inputs=2:duration=first[aout]")
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{duration:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", FILE_FINAL
    ]
    subprocess.run(cmd, check=True, cwd=BASE_DIR)

    final_duration = probe_duration(FILE_FINAL)
    if not (VIDEO_MIN_SECONDS <= final_duration <= VIDEO_MAX_SECONDS):
        raise RuntimeError(f"Video QA gagal: durasi {final_duration:.1f}s.")
    return final_duration


def send_telegram(video_path, caption):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return False
    try:
        with open(video_path, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo",
                              data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1000]},
                              files={"video": f}, timeout=120)
        r.raise_for_status()
        return True
    except Exception as exc:
        print(f"Telegram gagal: {exc}")
        return False

def get_instagram_id():
    if not FB_TOKEN or not FB_PAGE_ID:
        return None
    data = http_json("GET", f"{FB_API}/{FB_PAGE_ID}",
                     params={"fields": "instagram_business_account", "access_token": FB_TOKEN})
    return (data.get("instagram_business_account") or {}).get("id")

def post_instagram(video_path, caption, ig_id):
    if not ig_id:
        return False
    init = http_json("POST", f"{FB_API}/{ig_id}/media",
                     data={"media_type": "REELS", "upload_type": "resumable",
                           "caption": caption, "share_to_feed": "true", "access_token": FB_TOKEN})
    creation_id, upload_uri = init.get("id"), init.get("uri")
    if not creation_id or not upload_uri:
        raise RuntimeError("Instagram resumable init tidak mengembalikan ID/URI.")
    with open(video_path, "rb") as f:
        video = f.read()
    upload = requests.post(upload_uri, headers={"Authorization": f"OAuth {FB_TOKEN}",
                                                "offset": "0", "file_size": str(len(video))},
                           data=video, timeout=180)
    upload.raise_for_status()
    status = "UNKNOWN"
    for _ in range(24):
        time.sleep(5)
        data = http_json("GET", f"{FB_API}/{creation_id}",
                         params={"fields": "status_code,status", "access_token": FB_TOKEN}, timeout=15)
        status = data.get("status_code") or data.get("status")
        if status in {"FINISHED", "PUBLISHED"}:
            break
        if status in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Instagram processing gagal: {data}")
    if status not in {"FINISHED", "PUBLISHED"}:
        raise RuntimeError(f"Instagram processing timeout: {status}")
    pub = http_json("POST", f"{FB_API}/{ig_id}/media_publish",
                    data={"creation_id": creation_id, "access_token": FB_TOKEN})
    if not pub.get("id"):
        raise RuntimeError(f"Instagram publish gagal: {pub}")
    return True

def post_facebook(video_path, caption):
    if not (FB_TOKEN and FB_PAGE_ID):
        return False
    init = http_json("POST", f"{FB_API}/{FB_PAGE_ID}/video_reels",
                     data={"upload_phase": "start", "access_token": FB_TOKEN})
    video_id, upload_url = init.get("video_id"), init.get("upload_url")
    if not video_id or not upload_url:
        raise RuntimeError(f"Facebook Reel init gagal: {init}")
    with open(video_path, "rb") as f:
        video = f.read()
    upload = requests.post(upload_url, headers={"Authorization": f"OAuth {FB_TOKEN}",
                                                "offset": "0", "file_size": str(len(video))},
                           data=video, timeout=180)
    upload.raise_for_status()
    pub = http_json("POST", f"{FB_API}/{FB_PAGE_ID}/video_reels",
                    data={"upload_phase": "finish", "access_token": FB_TOKEN,
                          "video_id": video_id, "video_state": "PUBLISHED",
                          "description": caption})
    if not pub.get("success"):
        raise RuntimeError(f"Facebook Reel publish gagal: {pub}")
    return True

def cleanup_runtime_artifacts():
    names = {"narasi_en.mp3", "narasi_en.ass", "hook_en.ass", "media_sources.json"}
    for name in names:
        path = os.path.join(BASE_DIR, name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            print(f"Cleanup gagal: {path}: {exc}")
    for name in os.listdir(BASE_DIR):
        if name.startswith("media_") and os.path.isfile(os.path.join(BASE_DIR, name)):
            try:
                os.remove(os.path.join(BASE_DIR, name))
            except OSError as exc:
                print(f"Cleanup gagal: {name}: {exc}")
    try:
        if os.path.exists(FILE_FINAL):
            os.remove(FILE_FINAL)
    except OSError as exc:
        print(f"Cleanup gagal: {FILE_FINAL}: {exc}")


def main():
    if POST_MODE not in {"disabled", "dry_run", "production"}:
        raise RuntimeError("POST_MODE harus disabled, dry_run, atau production.")
    if POST_MODE == "disabled":
        print("REELS DISABLED: pipeline tidak membuat/post video. Mode aman.")
        return

    try:
        if POST_MODE == "production":
            time.sleep(random.randint(60, 480))

        item = choose_target()
        content_format = choose_content_format(item)
        print(f"Target: {item['latin']} / {item['indonesia']} | format={content_format}")

        media = download_species_media(item["latin"], item["indonesia"])
        hook = generate_english_hook(item, content_format)
        script = generate_english_script(item, content_format)
        segments = segment_script(script)
        duration, script = asyncio.run(make_tts_and_subtitles(script, segments))
        final_duration = render_reel(media, hook, duration)

        caption = (
            f"{item['indonesia']} ({item['latin']}) — Java biodiversity.\\n\\n"
            f"{item['fakta_singkat']}\\n\\n"
            f"Did you know this species?\\n\\n"
            f"#JavaWildlife #IndonesiaWildlife #FloraFaunaJawa #Biodiversity"
        )
        footage_count = sum(1 for x in media if x["kind"] == "video")
        print(
            f"VIDEO QA OK: {final_duration:.1f}s; voice={VOICE}; "
            f"media={len(media)}; footage={footage_count}; format={content_format}"
        )

        if POST_MODE == "dry_run":
            try:
                if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
                    raise RuntimeError("Dry-run membutuhkan TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID.")
                if not send_telegram(FILE_FINAL, caption):
                    raise RuntimeError("Telegram review gagal; dry-run dianggap gagal.")
                print("DRY RUN OK: video rendered and delivered to Telegram; no publishing and no history update.")
            finally:
                cleanup_runtime_artifacts()
            return

        previous = get_last_platform_status(item["latin"])
        status = {
            "facebook": previous.get("facebook", "failed"),
            "instagram": previous.get("instagram", "failed"),
            "telegram": previous.get("telegram", "failed"),
        }

        try:
            if status["telegram"] != "success":
                status["telegram"] = "success" if send_telegram(FILE_FINAL, caption) else "failed"
            if status["facebook"] != "success":
                status["facebook"] = "success" if post_facebook(FILE_FINAL, caption) else "failed"
            if status["instagram"] != "success":
                ig_id = get_instagram_id()
                if not ig_id:
                    raise RuntimeError("Instagram Business Account ID tidak ditemukan.")
                status["instagram"] = "success" if post_instagram(FILE_FINAL, caption, ig_id) else "failed"
        finally:
            save_history_record(item["latin"], {
                **status,
                "format": content_format,
                "media_count": len(media),
                "footage_count": footage_count,
            })
            cleanup_runtime_artifacts()

        if status["facebook"] != "success" or status["instagram"] != "success":
            raise RuntimeError(f"Publishing incomplete: {status}")
        print("REELS PRODUCTION SUCCESS:", status)

    finally:
        cleanup_runtime_artifacts()

if __name__ == "__main__":
    main()
