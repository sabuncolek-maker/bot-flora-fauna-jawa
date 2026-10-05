import os
import sys
import re
import json
import random
import asyncio
import subprocess
import requests
import edge_tts
from groq import Groq

# Mengunci folder kerja otomatis
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_AUDIO = os.path.join(BASE_DIR, "narasi.mp3")
FILE_SRT = os.path.join(BASE_DIR, "narasi.srt")
FILE_FINAL = os.path.join(BASE_DIR, "reels_30detik.mp4")

GROQ_KEY = str(os.environ.get("GROQ_API_KEY") or "").strip()
if not GROQ_KEY:
    raise ValueError("GROQ_API_KEY belum terpasang di GitHub Secrets!")

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ==========================================
# 1. Target Spesies Liar Jawa (GBIF)
# ==========================================
def get_species_target():
    try:
        polygon_jawa = "POLYGON((105.1 -5.8, 114.6 -5.8, 114.6 -8.8, 105.1 -8.8, 105.1 -5.8))"
        url = "https://api.gbif.org/v1/occurrence/search"
        params = {
            "country": "ID",
            "geometry": polygon_jawa,
            "iucnRedListCategory": ["CR", "EN", "VU"],
            "hasCoordinate": "true",
            "limit": 30,
            "offset": random.randint(0, 100)
        }
        res = requests.get(url, params=params, headers=HEADERS_BROWSER, timeout=12).json()
        results = res.get("results", [])
        kandidat = [item.get("species") for item in results if item.get("species")]
        if kandidat:
            return random.choice(list(set(kandidat)))
    except Exception as e:
        print(f"Kendala GBIF: {e}")
    return random.choice(["Panthera pardus melas", "Nisaetus bartelsi", "Presbytis comata"])

# ==========================================
# 2. Ambil Foto Alam Liar (Prioritas iNaturalist)
# ==========================================
def download_3_photos(scientific_name):
    """Mengumpulkan 3 foto alam asli dan menghindari gambar sketsa berlatar putih"""
    urls = []
    
    # 1. Tarik foto observasi lapangan dari fotografer iNaturalist
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

    # 2. Lengkapi dari Wikipedia jika foto lapangan kurang
    if len(urls) < 3:
        try:
            url_wiki = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(scientific_name)}"
            r = requests.get(url_wiki, headers=HEADERS_BROWSER, timeout=10).json()
            if "originalimage" in r:
                w_url = r["originalimage"]["source"]
                # Tolak gambar sketsa, diagram, peta, atau file SVG
                kata_tolak = [".svg", "map", "range", "drawing", "illustration", "plate"]
                if w_url not in urls and not any(k in w_url.lower() for k in kata_tolak):
                    urls.append(w_url)
        except Exception:
            pass

    # 3. Foto cadangan alam liar hutan tropis
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
    Style: BBC Earth or National Geographic documentary.
    Length: Exactly 4 distinct sentences, total 60-65 words.
    Format: Return ONLY the narration text. No markdown, no titles.
    """
    completion = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5
    )
    naskah = completion.choices[0].message.content.strip().replace('"', '')
    print(f"Naskah:\n{naskah}\n")
    return naskah

# ==========================================
# 4. Audio Narasi & Generator Subtitle Per Kalimat
# ==========================================
def format_srt_time(seconds):
    """Mengubah detik menjadi format waktu standar subtitle jam:menit:detik,milidetik"""
    millis = int((seconds - int(seconds)) * 1000)
    secs = int(seconds) % 60
    mins = int(seconds // 60) % 60
    hours = int(seconds // 3600)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

async def create_audio_and_clean_subtitles(text):
    # 1. Simpan suara narator
    voice = "en-US-ChristopherNeural"
    tts = edge_tts.Communicate(text, voice)
    await tts.save(FILE_AUDIO)

    # 2. Potong naskah menjadi beberapa kalimat terpisah
    kalimat_list = [k.strip() for k in re.split(r'(?<=[.!?])\s+', text) if k.strip()]
    if not kalimat_list:
        kalimat_list = [text]

    # Bagi durasi 28 detik secara proporsional sesuai jumlah kata per kalimat
    total_kata = sum(len(k.split()) for k in kalimat_list)
    durasi_total = 28.0
    waktu_mulai = 0.5
    
    srt_lines = []
    for i, kalimat in enumerate(kalimat_list, start=1):
        kata_kalimat = len(kalimat.split())
        durasi_kalimat = (kata_kalimat / total_kata) * durasi_total
        waktu_selesai = waktu_mulai + durasi_kalimat

        # Batasi panjang baris teks agar tidak melebar keluar batas layar
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
    print("Subtitle bersih per kalimat berhasil dibuat!")

# ==========================================
# 5. Render Video & Hardsub Rapi
# ==========================================
def render_multi_photo_reels(photo_files):
    clip_files = []
    
    # 1. Bikin 3 klip video @ 10 detik dengan kanvas blur
    for idx, photo in enumerate(photo_files, start=1):
        clip_output = os.path.join(BASE_DIR, f"clip_{idx}.mp4")
        print(f"Merender Klip {idx}...")
        
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

    # 2. Siapkan file concat
    concat_txt = os.path.join(BASE_DIR, "concat_list.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for c in clip_files:
            f.write(f"file '{c}'\n")

    # 3. Tempelkan subtitle rapi di bagian bawah layar
    # FontSize=13 adalah ukuran proporsional di FFmpeg agar teksnya ringkas 1-2 baris
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
    print("Render final sukses:", FILE_FINAL)

# ==========================================
# Alur Utama
# ==========================================
def main():
    target = get_species_target()
    print(f"Target Spesies Reels: {target}")

    photos = download_3_photos(target)
    naskah = generate_english_script(target)
    asyncio.run(create_audio_and_clean_subtitles(naskah))
    render_multi_photo_reels(photos)

if __name__ == "__main__":
    main()
