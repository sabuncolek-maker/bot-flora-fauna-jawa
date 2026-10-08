import os
import re
import json
import time
import random
import asyncio
import subprocess
import hashlib
from datetime import datetime, timezone

import requests
import edge_tts
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
MIN_PHOTOS = 4
MAX_PHOTOS = 6
MIN_UNIQUE_PHOTOS = 4
VIDEO_MIN_SECONDS = 22.0
VIDEO_MAX_SECONDS = 35.0
FB_API = "https://graph.facebook.com/v21.0"
HEADERS = {"User-Agent": "FloraFaunaJawa/2.0"}

GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
FB_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not GROQ_KEY:
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
    raise RuntimeError(f"HTTP gagal setelah {retries} percobaan: {url}") from last

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
    raise RuntimeError(f"Download gambar gagal setelah {retries} percobaan: {url}") from last

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
    required = {"latin", "indonesia", "fakta_hook", "fakta_singkat"}
    for item in data:
        if not required.issubset(item):
            raise RuntimeError(f"Entry spesies tidak lengkap: {item}")
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
    return any(x in raw for x in ["cc0", "cc by", "cc-by", "public domain"])


def get_media_candidates(latin):
    candidates = []
    seen_urls = set()

    def add(url, kind, source, meta=None):
        if not url or url in seen_urls:
            return
        lower = url.lower()
        if any(token in lower for token in [".svg", "map", "range", "distribution", "illustration", "plate"]):
            return
        seen_urls.add(url)
        candidates.append({"url": url, "kind": kind, "source": source, **(meta or {})})

    try:
        data = http_json(
            "GET", "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "generator": "search", "gsrsearch": latin,
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
                    "per_page": 50, "page": page,
                }, timeout=20,
            )
            results = data.get("results", [])
            for obs in results:
                for photo in obs.get("photos", []):
                    license_code = (photo.get("license_code") or "").lower()
                    if license_code and license_code not in {"cc0", "cc-by", "cc-by-sa"}:
                        continue
                    url = (photo.get("url") or "").replace("/square.", "/large.")
                    add(url, "photo", "iNaturalist", {
                        "license": license_code or "license-not-returned",
                        "observation_id": obs.get("id"),
                    })
            if not results:
                break
    except Exception as exc:
        print(f"iNaturalist gagal: {exc}")

    try:
        data = http_json(
            "GET", "https://commons.wikimedia.org/w/api.php",
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

    try:
        data = http_json(
            "GET",
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(latin)}",
            timeout=15,
        )
        add((data.get("originalimage") or {}).get("source"), "photo", "Wikimedia/Wikipedia",
            {"license": "verify Commons license"})
    except Exception as exc:
        print(f"Wikipedia gagal: {exc}")

    return candidates


def download_species_media(latin):
    candidates = get_media_candidates(latin)
    saved = []
    hashes = set()
    metadata = []

    for candidate in candidates:
        if candidate["kind"] != "video" or sum(x["kind"] == "video" for x in saved) >= 2:
            continue
        try:
            data = http_bytes(candidate["url"])
            digest = hashlib.sha256(data).hexdigest()
            if digest in hashes:
                continue
            mime = (candidate.get("mime") or "").lower()
            ext = ".webm" if "webm" in mime else ".ogv" if "ogg" in mime else ".mp4"
            path = os.path.join(BASE_DIR, f"media_{len(saved) + 1}{ext}")
            with open(path, "wb") as f:
                f.write(data)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type,duration,width,height",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=15
            )
            if probe.returncode != 0 or not probe.stdout.strip():
                os.remove(path)
                continue
            hashes.add(digest)
            saved.append({"path": path, "kind": "video"})
            metadata.append({**candidate, "path": path})
            print(f"Footage unik diterima: {len(saved)} | {candidate['source']} | {candidate.get('title', '')}")
        except Exception as exc:
            print(f"Footage ditolak: {candidate['url']} ({exc})")

    for candidate in candidates:
        if candidate["kind"] != "photo" or len(saved) >= MAX_PHOTOS:
            continue
        try:
            data = http_bytes(candidate["url"])
            digest = hashlib.sha256(data).hexdigest()
            if digest in hashes:
                continue
            path = os.path.join(BASE_DIR, f"media_{len(saved) + 1}.jpg")
            with open(path, "wb") as f:
                f.write(data)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=10
            )
            if probe.returncode != 0 or not probe.stdout.strip():
                os.remove(path)
                continue
            hashes.add(digest)
            saved.append({"path": path, "kind": "photo"})
            metadata.append({**candidate, "path": path})
            print(f"Foto unik diterima: {len(saved)} | {candidate['source']}")
        except Exception as exc:
            print(f"Foto ditolak: {candidate['url']} ({exc})")

    if len(saved) < MIN_UNIQUE_PHOTOS:
        raise RuntimeError(
            f"Hanya {len(saved)} media UNIK spesifik valid ditemukan untuk {latin}; "
            f"minimum {MIN_UNIQUE_PHOTOS}. Tidak akan mengulang media yang sama."
        )

    random.shuffle(saved)
    with open(os.path.join(BASE_DIR, "media_sources.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return saved


def choose_content_format(item):
    fact = item["fakta_singkat"].lower()
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

    def fallback():
        templates = {
            "guess": f"Can you identify this animal? It is {common_name}, known scientifically as {species}. {fact}",
            "one_fact": f"Here is one fact worth remembering about {common_name}. {fact}",
            "detective": f"Wildlife case file: identify the species from the evidence. The answer is {common_name}, {species}. {fact}",
            "myth_fact": f"Myth or fact? {fact} This statement is presented as a fact.",
            "baby_adult": f"Look closely at the young and adult stages of {common_name}. {fact}",
            "threatened": f"This is {common_name}, {species}. {fact} Protecting its habitat matters.",
            "java_file": f"Java wildlife file: {common_name}, {species}. {fact}",
        }
        return re.sub(r"\s+", " ", templates.get(content_format, templates["java_file"])).strip()

    try:
        client = Groq(api_key=GROQ_KEY)
        instructions = {
            "guess": "Build a curiosity-first identification puzzle. Do not reveal the answer until near the end.",
            "one_fact": "Open immediately with the single most surprising fact. No generic introduction.",
            "detective": "Write it like a mini wildlife investigation with two or three clues, then reveal the species.",
            "myth_fact": "Start with 'Myth or fact?' Present the supplied fact and clearly reveal the verdict.",
            "baby_adult": "Focus on the visual difference between young and adult stages, using only the supplied fact.",
            "threatened": "Use a serious documentary tone and emphasize the supplied threatened-status fact without exaggerating.",
            "java_file": "Make it feel like a fast 20-second wildlife case file about Java.",
        }
        prompt = f"""Write a high-retention English narration for a short vertical wildlife Reel about {species} ({common_name}) from Java, Indonesia.

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
    if 30 <= len(text.split()) <= 70:
        print(f"Using deterministic English narration fallback ({len(text.split())} words).")
        return text
    raise RuntimeError("English narration fallback failed QA.")


def generate_english_hook(item, content_format):
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
        if duration > VIDEO_MAX_SECONDS:
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
    per_media = duration / len(media)
    cmd = ["ffmpeg", "-y"]
    filters = []
    fps = 25

    for i, item in enumerate(media):
        path = item["path"]
        kind = item["kind"]
        cmd += ["-stream_loop", "-1"] if kind == "video" else ["-loop", "1"]
        cmd += ["-t", f"{per_media:.3f}", "-i", path]
        frames = max(1, round(per_media * fps))
        motion = "1+0.00045*on" if i % 2 == 0 else "max(1.08-0.00045*on\\\\,1.0)"

        if kind == "video":
            filters.append(
                f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"boxblur=15:3[bg{i}];"
                f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg{i}];"
                f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,setsar=1[v{i}]"
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
    filters.append(f"{labels}concat=n={len(media)}:v=1:a=0,setpts=PTS-STARTPTS[vbase]")

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

def main():
    if POST_MODE not in {"disabled", "dry_run", "production"}:
        raise RuntimeError("POST_MODE harus disabled, dry_run, atau production.")
    if POST_MODE == "disabled":
        print("REELS DISABLED: pipeline tidak membuat/post video. Mode aman.")
        return

    if POST_MODE == "production":
        time.sleep(random.randint(60, 480))

    item = choose_target()
    content_format = choose_content_format(item)
    print(f"Target: {item['latin']} / {item['indonesia']} | format={content_format}")

    media = download_species_media(item["latin"])
    hook = generate_english_hook(item, content_format)
    script = generate_english_script(item, content_format)
    segments = segment_script(script)
    duration, script = asyncio.run(make_tts_and_subtitles(script, segments))
    final_duration = render_reel(media, hook, duration)

    caption = (
        f"{item['indonesia']} ({item['latin']}) — Java wildlife.\\n\\n"
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
        if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
            raise RuntimeError("Dry-run membutuhkan TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID.")
        if not send_telegram(FILE_FINAL, caption):
            raise RuntimeError("Telegram review gagal; dry-run dianggap gagal.")
        print("DRY RUN OK: video rendered and delivered to Telegram; no Facebook/Instagram publishing and no history update.")
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

    if status["facebook"] != "success" or status["instagram"] != "success":
        raise RuntimeError(f"Publishing incomplete: {status}")
    print("REELS PRODUCTION SUCCESS:", status)

if __name__ == "__main__":
    main()
