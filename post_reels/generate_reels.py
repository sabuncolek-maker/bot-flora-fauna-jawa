import os
import re
import json
import time
import random
import asyncio
import subprocess
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
                (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
            )
            if not valid_magic or ("image/" not in content_type and not content_type.startswith("application/octet-stream")):
                raise RuntimeError("Response bukan file gambar valid.")
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
    history = {x.get("species") for x in load_history() if isinstance(x, dict)}
    available = [s for s in species if s["latin"] not in history]
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

def get_photo_candidates(latin):
    """
    Return only species-specific image candidates.
    Source priority:
      1) iNaturalist Research Grade observations
      2) Wikimedia Commons file search
      3) Wikipedia REST image as a final species-specific fallback
    No generic stock-image fallback is allowed.
    """
    urls = []
    sources = []

    def add(url, meta):
        if not url or url in urls:
            return False
        lower = url.lower()
        if any(token in lower for token in [".svg", "map", "range", "distribution", "illustration", "plate"]):
            return False
        urls.append(url)
        sources.append(meta)
        return True

    # iNaturalist: gather from several observations/pages so one observation
    # or one license filter cannot leave us with too few usable images.
    try:
        for page in range(1, 4):
            data = http_json(
                "GET",
                "https://api.inaturalist.org/v1/observations",
                params={
                    "taxon_name": latin,
                    "has[]": "photos",
                    "quality_grade": "research",
                    "per_page": 50,
                    "page": page,
                },
                timeout=20,
            )
            results = data.get("results", [])
            for obs in results:
                for photo in obs.get("photos", []):
                    # Accept only explicitly licensed reusable photos.
                    license_code = (photo.get("license_code") or "").lower()
                    if license_code and license_code not in {"cc0", "cc-by", "cc-by-sa"}:
                        continue
                    url = (photo.get("url") or "").replace("/square.", "/large.")
                    if add(url, {
                        "source": "iNaturalist",
                        "url": url,
                        "license": license_code or "license-not-returned",
                    }) and len(urls) >= MAX_PHOTOS:
                        return list(zip(urls, sources))
            if not results:
                break
    except Exception as exc:
        print(f"iNaturalist gagal: {exc}")

    # Wikimedia Commons API is more reliable than the Wikipedia REST
    # summary endpoint and can return multiple species-specific files.
    try:
        data = http_json(
            "GET",
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": latin,
                "gsrnamespace": 6,
                "gsrlimit": 50,
                "prop": "imageinfo",
                "iiprop": "url|mime",
                "iiurlwidth": 1600,
                "format": "json",
                "formatversion": 2,
            },
            timeout=20,
        )
        for page in data.get("query", {}).get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            mime = (info.get("mime") or "").lower()
            url = info.get("thumburl") or info.get("url")
            if not mime.startswith("image/") or mime == "image/svg+xml":
                continue
            if add(url, {
                "source": "Wikimedia Commons",
                "url": url,
                "license": "Commons file; verify file license metadata",
                "title": page.get("title"),
            }) and len(urls) >= MAX_PHOTOS:
                return list(zip(urls, sources))
    except Exception as exc:
        print(f"Wikimedia Commons gagal: {exc}")

    # Wikipedia REST: keep as a final fallback, but failure is non-fatal.
    try:
        data = http_json(
            "GET",
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(latin)}",
            timeout=15,
        )
        img = (data.get("originalimage") or {}).get("source")
        if add(img, {
            "source": "Wikimedia/Wikipedia",
            "url": img,
            "license": "verify Commons license",
        }) and len(urls) >= MAX_PHOTOS:
            return list(zip(urls, sources))
    except Exception as exc:
        print(f"Wikipedia gagal: {exc}")

    return list(zip(urls, sources))

def download_species_photos(latin):
    candidates = get_photo_candidates(latin)
    saved = []
    metadata = []
    for idx, (url, meta) in enumerate(candidates, 1):
        try:
            data = http_bytes(url)
            path = os.path.join(BASE_DIR, f"foto_{idx}.jpg")
            with open(path, "wb") as f:
                f.write(data)
            # ffprobe is a second-stage media sanity check.
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                    "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                                   capture_output=True, text=True, timeout=10)
            if probe.returncode != 0 or not probe.stdout.strip():
                continue
            saved.append(path)
            metadata.append(meta)
            if len(saved) >= MAX_PHOTOS:
                break
        except Exception as exc:
            print(f"Foto ditolak: {url} ({exc})")
    if len(saved) < MIN_PHOTOS:
        raise RuntimeError(f"Hanya {len(saved)} foto spesifik valid ditemukan untuk {latin}; minimum {MIN_PHOTOS}.")
    with open(os.path.join(BASE_DIR, "photo_sources.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return saved

def generate_english_script(item):
    """
    Generate English narration with a deterministic fallback.
    The fallback is intentional: a failed/empty LLM response must never stop
    the Reels pipeline when the curated editorial fact is already available.
    """
    fact = re.sub(r"\\s+", " ", str(item.get("fakta_singkat", "")).strip()).strip()
    species = item["latin"]
    common_name = item["indonesia"]

    def fallback():
        text = (
            f"Meet {species}, known in Indonesia as {common_name}. "
            f"{fact} "
            f"This species is part of Java's remarkable natural heritage. "
            f"Its story shows why careful observation and protection of wildlife matter. "
            f"From its distinctive traits to its place in the island's biodiversity, "
            f"{species} is a species worth knowing and respecting."
        )
        return re.sub(r"\\s+", " ", text).strip()

    # LLM is preferred for natural documentary phrasing, but it is not a
    # single point of failure.
    try:
        client = Groq(api_key=GROQ_KEY)
        base_prompt = f"""Write a short wildlife documentary narration in natural English about {species} ({common_name}) from Java, Indonesia.

ONLY use these editorial facts as factual claims:
- {fact}
Do not invent population numbers, locations, measurements, behavior, conservation status, superlatives, or habitat details.
Do not turn uncertain claims into absolute claims.
Write 55-90 words, aiming for about 65-80 words, suitable for roughly 25-32 seconds at a calm pace.
Tone: calm, cinematic, intelligent, documentary-style. No YouTuber language.
Return only the narration, no title, bullets, markdown, URLs, or citations."""
        last_count = 0

        for attempt in range(3):
            prompt = base_prompt
            if attempt:
                prompt += "\nIMPORTANT: Return a complete narration between 55 and 90 words. Do not return an empty response."
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.15,
                max_tokens=180,
            )
            raw = response.choices[0].message.content if response.choices else ""
            text = re.sub(r"\\s+", " ", (raw or "").strip()).strip()
            last_count = len(text.split())
            if 45 <= last_count <= 105:
                return text
            if last_count == 0:
                break
    except Exception as exc:
        print(f"Groq narration unavailable, using deterministic fallback: {exc}")

    text = fallback()
    count = len(text.split())
    if 45 <= count <= 105:
        print(f"Using deterministic English narration fallback ({count} words).")
        return text
    raise RuntimeError(f"Deterministic narration fallback failed QA ({count} words).")

def generate_english_hook(item):
    # Hook is derived from the curated editorial hook, translated/reframed,
    # never invented from a random LLM claim.
    hooks = {
        "BUNGA PARASIT LANGKA": "RARE PARASITIC BLOOM",
        "BUNGA ABADI GUNUNG": "THE MOUNTAIN EVERLASTING",
        "GARUDA INDONESIA": "JAVA'S ICONIC EAGLE",
        "HANTU HUTAN JAWA": "JAVA'S ELUSIVE CAT",
        "TERLANGKA DI DUNIA": "ONE OF EARTH'S RAREST RHINOS",
        "PENYANYI HUTAN": "THE FOREST SINGER",
        "MONYET BERJENGGOT": "JAVA'S BEARDED LEAF MONKEY",
        "BAYI EMAS": "THE GOLDEN BABY",
        "KODOK BERDARAH": "THE RED FROG",
        "PRIMATA BERBISA": "THE VENOMOUS PRIMATE",
        "SISIK BAJA": "THE ARMORED MAMMAL",
        "ANJING HUTAN": "JAVA'S WILD DOG",
        "MERAK ASLI JAWA": "JAVA'S GREEN PEAFOWL",
        "KATAK BISA TERBANG": "THE FLYING FROG",
        "KUCING HUTAN MINI": "THE TINY WILDCAT",
        "BANTENG LIAR": "JAVA'S WILD BANTENG",
        "RAKSASA PEMBERSIH": "THE FOREST CLEANER",
        "TANAMAN PEMANGSA": "THE CARNIVOROUS PLANT",
        "KOPI TERMAHAL DUNIA": "THE CIVET BEHIND KOPI LUWAK",
        "NAGA JAWA": "JAVA'S GIANT LIZARD",
    }
    return hooks.get(item["fakta_hook"], "WILDLIFE OF JAVA")

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
    tts_text = " ".join(segments)
    communicator = edge_tts.Communicate(
        tts_text, VOICE, rate="-12%", pitch="-2Hz"
    )
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
        raise RuntimeError(f"English TTS/WordBoundary gagal: {exc}") from exc
    if not audio_chunks or not bounds:
        raise RuntimeError("WordBoundary tidak tersedia; Reels dihentikan agar subtitle tidak menebak timing.")
    with open(FILE_AUDIO, "wb") as f:
        for chunk in audio_chunks:
            f.write(chunk)

    segment_times = []
    cursor = 0
    for segment in segments:
        words = [norm_word(w) for w in segment.split() if norm_word(w)]
        starts = []
        ends = []
        for word in words:
            found = None
            for j in range(cursor, len(bounds)):
                if bounds[j][0] == word:
                    found = j
                    break
            if found is None:
                raise RuntimeError(f"WordBoundary mapping gagal pada kata: {word}")
            starts.append(bounds[found][1])
            ends.append(bounds[found][2])
            cursor = found + 1
        segment_times.append((starts[0], ends[-1]))

    # Hook occupies the first 3 seconds, so both audio and subtitles start after it.
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
        for segment, (start, end) in zip(segments, shifted):
            safe = segment.replace("{", "").replace("}", "")
            f.write(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Narasi,{safe.upper()}\\N\n")
    duration = probe_duration(FILE_AUDIO) + offset
    if duration < VIDEO_MIN_SECONDS or duration > VIDEO_MAX_SECONDS:
        raise RuntimeError(f"Final duration {duration:.1f}s di luar {VIDEO_MIN_SECONDS}-{VIDEO_MAX_SECONDS}s.")
    return duration

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

def render_reel(photos, hook, duration):
    per_photo = duration / len(photos)
    cmd = ["ffmpeg", "-y"]
    filters = []
    fps = 25
    for i, photo in enumerate(photos):
        cmd += ["-loop", "1", "-t", f"{per_photo:.3f}", "-i", photo]
        frames = max(1, round(per_photo * fps))
        motion = "1+0.00045*on" if i % 2 == 0 else "max(1.08-0.00045*on\\,1.0)"
        filters.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            f"boxblur=15:3[bg{i}];"
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg{i}];"
            f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,scale=2160:3840,"
            f"zoompan=z='{motion}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps={fps},"
            f"setsar=1[v{i}]"
        )
    labels = "".join(f"[v{i}]" for i in range(len(photos)))
    filters.append(f"{labels}concat=n={len(photos)}:v=1:a=0,setpts=PTS-STARTPTS[vbase]")
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
        f.write(f"Dialogue: 0,0:00:00.50,0:00:03.00,Hook,{hook_clean}\n")

    idx_audio = len(photos)
    cmd += ["-i", FILE_AUDIO]
    filters.append(f"[vbase]ass=hook_en.ass,ass=narasi_en.ass[vout]")
    filters.append(f"anoisesrc=color=brown:duration=40:sample_rate=44100[noise];[noise]lowpass=f=400,volume=0.06[amb]")
    filters.append(f"[{idx_audio}:a]adelay=3000|3000[narr];[narr][amb]amix=inputs=2:duration=first[aout]")
    cmd += ["-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]",
            "-t", f"{duration:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", FILE_FINAL]
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
    print(f"Target: {item['latin']} / {item['indonesia']}")
    photos = download_species_photos(item["latin"])
    hook = generate_english_hook(item)
    script = generate_english_script(item)
    segments = segment_script(script)
    duration = asyncio.run(make_tts_and_subtitles(script, segments))
    final_duration = render_reel(photos, hook, duration)

    caption = (
        f"{item['indonesia']} ({item['latin']}) — wildlife of Java, Indonesia.\\n\\n"
        f"{script}\\n\\n#JavaWildlife #IndonesiaWildlife #FloraFaunaJawa #Wildlife #Biodiversity"
    )
    print(f"VIDEO QA OK: {final_duration:.1f}s; voice={VOICE}; photos={len(photos)}")

    if POST_MODE == "dry_run":
        send_telegram(FILE_FINAL, caption)
        print("DRY RUN: no Facebook/Instagram publishing and no history update.")
        return

    # Production only reaches here after all content/media QA above.
    status = {"facebook": "failed", "instagram": "failed", "telegram": "failed"}
    try:
        status["telegram"] = "success" if send_telegram(FILE_FINAL, caption) else "failed"
        status["facebook"] = "success" if post_facebook(FILE_FINAL, caption) else "failed"
        ig_id = get_instagram_id()
        if not ig_id:
            raise RuntimeError("Instagram Business Account ID tidak ditemukan.")
        status["instagram"] = "success" if post_instagram(FILE_FINAL, caption, ig_id) else "failed"
    finally:
        save_history_record(item["latin"], status)
    if status["facebook"] != "success" or status["instagram"] != "success":
        raise RuntimeError(f"Publishing incomplete: {status}")
    print("REELS PRODUCTION SUCCESS:", status)

if __name__ == "__main__":
    main()
