import os
import sys
import re
import json
import time
import random
import asyncio
import subprocess
import requests
import edge_tts
from groq import Groq

# ==========================================
# 0. Konfigurasi Sistem & Kredensial
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_AUDIO = os.path.join(BASE_DIR, "narasi.mp3")
FILE_SRT = os.path.join(BASE_DIR, "narasi.srt")
FILE_FINAL = os.path.join(BASE_DIR, "reels_30detik.mp4")

GROQ_KEY = str(os.environ.get("GROQ_API_KEY") or "").strip()
FB_TOKEN = str(os.environ.get("FB_PAGE_ACCESS_TOKEN") or "").strip()
FB_PAGE_ID = str(os.environ.get("FB_PAGE_ID") or "").strip()
TELEGRAM_TOKEN = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

if not GROQ_KEY:
    raise ValueError("GROQ_API_KEY belum terpasang di GitHub Secrets!")

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ==========================================
# 1. Target Spesies Liar Jawa (GBIF API)
# ==========================================
def load_history():
    """Membaca daftar spesies yang sudah pernah diunggah"""
    if os.path.exists(FILE_HISTORY):
        try:
            with open(FILE_HISTORY, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_to_history(species_name):
    """Menyimpan spesies baru ke buku catatan riwayat (maksimal simpan 60 riwayat terakhir)"""
    history = load_history()
    if species_name not in history:
        history.append(species_name)
    if len(history) > 60:
        history = history[-60:]
    with open(FILE_HISTORY, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

def get_species_target():
    """Mengambil spesies yang belum pernah diposting dalam sebulan terakhir"""
    history = load_history()
    kandidat = []
    try:
        polygon_jawa = "POLYGON((105.1 -5.8, 114.6 -5.8, 114.6 -8.8, 105.1 -8.8, 105.1 -5.8))"
        url = "https://api.gbif.org/v1/occurrence/search"
        params = {
            "country": "ID",
            "geometry": polygon_jawa,
            "iucnRedListCategory": ["CR", "EN", "VU"],
            "hasCoordinate": "true",
            "limit": 40,
            "offset": random.randint(0, 150)
        }
        res = requests.get(url, params=params, headers=HEADERS_BROWSER, timeout=12).json()
        results = res.get("results", [])
        semua_spesies = list(set([item.get("species") for item in results if item.get("species")]))
        # Filter: ambil yang belum ada di catatan riwayat
        kandidat = [s for s in semua_spesies if s not in history]
    except Exception as e:
        print(f"Kendala GBIF: {e}")

    if kandidat:
        return random.choice(kandidat)
    
    cadangan = ["Panthera pardus melas", "Nisaetus bartelsi", "Presbytis comata", "Leptophryne cruentata", "Prionailurus bengalensis"]
    sisa = [c for c in cadangan if c not in history]
    return random.choice(sisa if sisa else cadangan)

# ==========================================
# 2. Ambil Foto Alam Liar (Prioritas iNaturalist)
# ==========================================
def download_3_photos(scientific_name):
    urls = []
    try:
        url_inat = f"https://api.inaturalist.org/v1/observations?taxon_name={requests.utils.quote(scientific_name)}&has[]=photos&quality_grade=research&per_page=10"
        res_inat = requests.get(url_inat, headers=HEADERS_BROWSER, timeout=10).json()
        for item in res_inat.get("results", []):
            for photo in item.get("photos", []):
                link = photo.get("url", "").replace("square", "large")
                if link and link not in urls:
                    urls.append(link)
                if len(urls) >= 3:
                    break
            if len(urls) >= 3:
                break
    except Exception as e:
        print(f"Kendala iNaturalist: {e}")

    if len(urls) < 3:
        try:
            url_wiki = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(scientific_name)}"
            r = requests.get(url_wiki, headers=HEADERS_BROWSER, timeout=10).json()
            if "originalimage" in r:
                w_url = r["originalimage"]["source"]
                kata_tolak = [".svg", "map", "range", "drawing", "illustration", "plate"]
                if w_url not in urls and not any(k in w_url.lower() for k in kata_tolak):
                    urls.append(w_url)
        except Exception:
            pass

    cadangan = [
        "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w=1080",
        "https://images.unsplash.com/photo-1448375240586-882707db888b?w=1080",
        "https://images.unsplash.com/photo-1470071459604-3b5ec3a7fe05?w=1080"
    ]
    for c in cadangan:
        if len(urls) >= 3:
            break
        if c not in urls:
            urls.append(c)

    saved_files = []
    for idx, img_url in enumerate(urls[:3], start=1):
        file_path = os.path.join(BASE_DIR, f"foto_{idx}.jpg")
        try:
            r = requests.get(img_url, headers=HEADERS_BROWSER, timeout=15)
            if r.status_code == 200 and len(r.content) > 8000:
                with open(file_path, "wb") as f:
                    f.write(r.content)
                saved_files.append(file_path)
            else:
                r_fallback = requests.get(cadangan[idx-1], headers=HEADERS_BROWSER, timeout=15)
                with open(file_path, "wb") as f:
                    f.write(r_fallback.content)
                saved_files.append(file_path)
        except Exception:
            pass
            
    return saved_files

# ==========================================
# 3. Riset Naskah Narasi (Groq AI)
# ==========================================
def generate_english_script(scientific_name):
    client = Groq(api_key=GROQ_KEY)
    prompt = f"""
    Write a dramatic wildlife documentary narration about '{scientific_name}' from Java Island.
    Style: BBC Earth or National Geographic documentary narration.
    Length: Exactly 4 distinct sentences, total 60-65 words.
    Format: Return ONLY the plain English narration text. No markdown, no titles.
    """
    completion = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5
    )
    naskah = completion.choices[0].message.content.strip().replace('"', '')
    print(f"Naskah Narasi:\n{naskah}\n")
    return naskah

# ==========================================
# 4. Audio Narasi & Subtitle Per Kalimat
# ==========================================
def format_srt_time(seconds):
    millis = int((seconds - int(seconds)) * 1000)
    secs = int(seconds) % 60
    mins = int(seconds // 60) % 60
    hours = int(seconds // 3600)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

async def create_audio_and_clean_subtitles(text):
    voice = "en-US-ChristopherNeural"
    tts = edge_tts.Communicate(text, voice)
    await tts.save(FILE_AUDIO)

    kalimat_list = [k.strip() for k in re.split(r'(?<=[.!?])\s+', text) if k.strip()]
    if not kalimat_list:
        kalimat_list = [text]

    total_kata = sum(len(k.split()) for k in kalimat_list)
    durasi_total = 28.0
    waktu_mulai = 0.5
    
    srt_lines = []
    for i, kalimat in enumerate(kalimat_list, start=1):
        kata_kalimat = len(kalimat.split())
        durasi_kalimat = (kata_kalimat / total_kata) * durasi_total
        waktu_selesai = waktu_mulai + durasi_kalimat

        kata_per_kata = kalimat.split()
        if len(kata_per_kata) > 7:
            tengah = len(kata_per_kata) // 2
            kalimat_rapi = " ".join(kata_per_kata[:tengah]) + "\\N" + " ".join(kata_per_kata[tengah:])
        else:
            kalimat_rapi = kalimat

        srt_lines.append(f"{i}\n{format_srt_time(waktu_mulai)} --> {format_srt_time(waktu_selesai)}\n{kalimat_rapi}\n")
        waktu_mulai = waktu_selesai

    with open(FILE_SRT, "w", encoding="utf-8") as f_sub:
        f_sub.write("\n".join(srt_lines))
    print("Audio MP3 dan Subtitle SRT selesai dibuat!")

# ==========================================
# 5. Render Video 3 Foto & Subtitle
# ==========================================
def render_multi_photo_reels(photo_files):
    clip_files = []
    
    for idx, photo in enumerate(photo_files, start=1):
        clip_output = os.path.join(BASE_DIR, f"clip_{idx}.mp4")
        filter_str = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
            "zoompan=z='min(zoom+0.0006,1.08)':d=250:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=25"
        )
        cmd_clip = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", photo,
            "-t", "10",
            "-filter_complex", filter_str,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            clip_output
        ]
        subprocess.run(cmd_clip, check=True, cwd=BASE_DIR)
        clip_files.append(clip_output)

    concat_txt = os.path.join(BASE_DIR, "concat_list.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for c in clip_files:
            f.write(f"file '{c}'\n")

    sub_filter = (
        "subtitles=narasi.srt:force_style='Alignment=2\\,"
        "FontSize=8\\,"
        "Bold=1\\,"
        "PrimaryColour=&H00FFFFFF\\,"
        "OutlineColour=&H00000000\\,"
        "BorderStyle=1\\,"
        "Outline=1\\,"
        "MarginV=25'"
    )

    cmd_merge = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_txt,
        "-i", FILE_AUDIO,
        "-vf", sub_filter,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        FILE_FINAL
    ]
    subprocess.run(cmd_merge, check=True, cwd=BASE_DIR)
    print("Render final Reels sukses:", FILE_FINAL)

# ==========================================
# 6. Distribusi Telegram Bot
# ==========================================
def send_to_telegram(video_path, caption_text):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
        with open(video_path, "rb") as f:
            requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption_text[:1000]},
                files={"video": f},
                timeout=90
            )
        print("-> Video berhasil terkirim ke Telegram!")
    except Exception as e:
        print(f"Gagal mengirim ke Telegram: {e}")

# ==========================================
# 7. Ambil ID Akun Instagram Otomatis
# ==========================================
def get_instagram_id():
    """Mendeteksi ID Instagram Business otomatis dari Facebook Page ID"""
    try:
        url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}?fields=instagram_business_account&access_token={FB_TOKEN}"
        res = requests.get(url, timeout=10).json()
        ig_id = res.get("instagram_business_account", {}).get("id")
        return ig_id
    except Exception as e:
        print(f"Kendala mencari Instagram ID: {e}")
        return None

# ==========================================
# 8. Publikasi Instagram Reels
# ==========================================
def post_instagram_reels(video_path, caption_text, ig_id):
    if not ig_id:
        print("ID Instagram tidak ditemukan dari Page FB. Melewati posting Instagram.")
        return

    try:
        print(f"Menginisialisasi Instagram Reels (IG ID: {ig_id})...")
        init_url = f"https://graph.facebook.com/v19.0/{ig_id}/media"
        init_params = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption_text,
            "share_to_feed": "true",
            "access_token": FB_TOKEN
        }
        r_init = requests.post(init_url, data=init_params, timeout=20).json()
        video_id = r_init.get("id")
        upload_uri = r_init.get("uri")

        if not upload_uri:
            print(f"Gagal inisialisasi IG Reels: {r_init}")
            return

        with open(video_path, "rb") as f:
            video_data = f.read()

        headers = {
            "Authorization": f"OAuth {FB_TOKEN}",
            "offset": "0",
            "file_size": str(len(video_data))
        }
        requests.post(upload_uri, headers=headers, data=video_data, timeout=120)

        print("Menunggu proses pemrosesan video Instagram...")
        status_url = f"https://graph.facebook.com/v19.0/{video_id}?fields=status_code&access_token={FB_TOKEN}"
        for _ in range(12):
            time.sleep(10)
            status_res = requests.get(status_url, timeout=10).json()
            if status_res.get("status_code") == "FINISHED":
                break

        pub_url = f"https://graph.facebook.com/v19.0/{ig_id}/media_publish"
        pub_res = requests.post(pub_url, data={"creation_id": video_id, "access_token": FB_TOKEN}, timeout=20).json()
        print("-> SUKSES! Instagram Reels terbit. ID:", pub_res.get("id"))
    except Exception as e:
        print(f"Kendala saat posting Instagram Reels: {e}")

# ==========================================
# 9. Publikasi Facebook Reels
# ==========================================
def post_facebook_reels(video_path, caption_text):
    if not (FB_TOKEN and FB_PAGE_ID):
        print("Kredensial Facebook belum lengkap, lewati posting FB.")
        return

    try:
        print("Menginisialisasi Facebook Reels...")
        init_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/video_reels"
        r_init = requests.post(init_url, data={"upload_phase": "start", "access_token": FB_TOKEN}, timeout=20).json()
        video_id = r_init.get("video_id")
        upload_url = r_init.get("upload_url")

        if not upload_url:
            print(f"Gagal inisialisasi FB Reels: {r_init}")
            return

        with open(video_path, "rb") as f:
            video_data = f.read()

        headers = {
            "Authorization": f"OAuth {FB_TOKEN}",
            "offset": "0",
            "file_size": str(len(video_data))
        }
        requests.post(upload_url, headers=headers, data=video_data, timeout=120)

        print("Menerbitkan Facebook Reels ke Halaman...")
        publish_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/video_reels"
        pub_params = {
            "upload_phase": "finish",
            "access_token": FB_TOKEN,
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption_text
        }
        r_pub = requests.post(publish_url, data=pub_params, timeout=20).json()
        if r_pub.get("success"):
            print("-> SUKSES! Facebook Reels terbit di Halaman FB!")
        else:
            print(f"Respon penerbitan FB Reels: {r_pub}")
    except Exception as e:
        print(f"Kendala saat posting Facebook Reels: {e}")

# ==========================================
# Alur Eksekusi Utama
# ==========================================
def main():
    # Jeda acak 1 sampai 8 menit agar jam tayang tidak terbaca sebagai mesin kaku
    jeda_detik = random.randint(60, 480)
    print(f"Menunggu jeda alami selama {jeda_detik} detik sebelum memproses...")
    time.sleep(jeda_detik)

    target = get_species_target()
    print(f"Target Spesies Reels: {target}")

    photos = download_3_photos(target)
    naskah = generate_english_script(target)
    asyncio.run(create_audio_and_clean_subtitles(naskah))
    render_multi_photo_reels(photos)

    caption = (
        f"The hidden wildlife of Java: {target}.\n\n"
        f"{naskah}\n\n"
        f"#wildlife #indonesia #nature #documentary #indobizarre #javanwildlife #biodiversity"
    )

    send_to_telegram(FILE_FINAL, caption)
    post_facebook_reels(FILE_FINAL, caption)
    
    ig_id = get_instagram_id()
    if ig_id:
        post_instagram_reels(FILE_FINAL, caption, ig_id)

    # Simpan ke riwayat agar tidak diunggah ulang
    save_to_history(target)
    print("Spesies resmi dicatat ke history_reels.json!")
    else:
        print("Halaman Facebook belum terhubung ke Akun Bisnis Instagram.")

if __name__ == "__main__":
    main()
