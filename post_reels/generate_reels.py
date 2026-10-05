import os
import sys
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
# 1. Ambil Spesies Liar Jawa (GBIF API)
# ==========================================
def get_species_target():
    """Mengambil target spesies liar Jawa"""
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
# 2. Unduh 3 Foto Berbeda (iNaturalist & Wikipedia)
# ==========================================
def download_3_photos(scientific_name):
    """Mengumpulkan 3 gambar observasi berbeda agar video variatif"""
    urls = []
    
    # 1. Ambil foto-foto observasi liar dari iNaturalist API
    try:
        url_inat = f"https://api.inaturalist.org/v1/observations?taxon_name={requests.utils.quote(scientific_name)}&has[]=photos&quality_grade=research&per_page=6"
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

    # 2. Lengkapi dari Wikipedia jika kurang
    if len(urls) < 3:
        try:
            url_wiki = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(scientific_name)}"
            r = requests.get(url_wiki, headers=HEADERS_BROWSER, timeout=10).json()
            if "originalimage" in r:
                w_url = r["originalimage"]["source"]
                if w_url not in urls and not any(ext in w_url.lower() for ext in [".svg", "map", "range"]):
                    urls.append(w_url)
        except Exception:
            pass

    # 3. Foto cadangan alam
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

    # Simpan file fisik
    saved_files = []
    for idx, img_url in enumerate(urls[:3], start=1):
        file_path = os.path.join(BASE_DIR, f"foto_{idx}.jpg")
        try:
            r = requests.get(img_url, headers=HEADERS_BROWSER, timeout=15)
            if r.status_code == 200 and len(r.content) > 5000:
                with open(file_path, "wb") as f:
                    f.write(r.content)
                saved_files.append(file_path)
            else:
                r_fallback = requests.get(cadangan[idx-1], headers=HEADERS_BROWSER, timeout=15)
                with open(file_path, "wb") as f:
                    f.write(r_fallback.content)
                saved_files.append(file_path)
        except Exception as e:
            print(f"Gagal unduh foto {idx}: {e}")
            
    return saved_files

# ==========================================
# 3. Naskah Dokumenter 30 Detik (Groq AI)
# ==========================================
def generate_english_script(scientific_name):
    """Menyusun narasi pas 30 detik (65-70 kata)"""
    client = Groq(api_key=GROQ_KEY)
    prompt = f"""
    Write a dramatic, captivating wildlife documentary voiceover script about '{scientific_name}' from Java Island.
    Style: National Geographic or BBC Earth documentary narration.
    Length constraint: Exactly between 65 and 70 words (to precisely match 28-30 seconds speech duration).
    Output: Return ONLY the plain English narration text, nothing else. No titles, no bullet points, no markdown.
    """
    completion = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5
    )
    naskah = completion.choices[0].message.content.strip().replace('"', '')
    print(f"Naskah Narasi ({len(naskah.split())} kata):\n{naskah}\n")
    return naskah

# ==========================================
# 4. Rekam Suara + Buat Subtitle Otomatis
# ==========================================
async def generate_voiceover_and_subtitles(text):
    """Membuat file MP3 sekaligus file subtitle SRT otomatis"""
    voice = "en-US-ChristopherNeural"
    tts = edge_tts.Communicate(text, voice)
    submaker = edge_tts.SubMaker()

    with open(FILE_AUDIO, "wb") as f_audio:
        async for chunk in tts.stream():
            if chunk["type"] == "audio":
                f_audio.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)

    # Simpan naskah berwaktu ke format standar SubRip (.srt)
    srt_content = submaker.get_srt()
    if not srt_content.strip():
        # Fallback jika deteksi kata kosong: buat subtitle standar penuh durasi
        srt_content = f"1\n00:00:00,000 --> 00:00:29,000\n{text}\n"

    with open(FILE_SRT, "w", encoding="utf-8") as f_sub:
        f_sub.write(srt_content)

    print("Audio dan berkas subtitle SRT berhasil dibuat!")

# ==========================================
# 5. Render Video + Tempelkan Subtitle (Hardsub)
# ==========================================
def render_multi_photo_reels(photo_files):
    """Merender 3 foto dan menempelkan teks subtitle ke layar video"""
    clip_files = []
    
    # 1. Bikin 3 sub-klip video berkanvas blur (@ 10 detik)
    for idx, photo in enumerate(photo_files, start=1):
        clip_output = os.path.join(BASE_DIR, f"clip_{idx}.mp4")
        print(f"Merender Klip {idx}...")
        
        filter_str = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
            "zoompan=z='min(zoom+0.0008,1.1)':d=250:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=25"
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

    # 2. Siapkan daftar penggabungan video
    concat_txt = os.path.join(BASE_DIR, "concat_list.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for c in clip_files:
            f.write(f"file '{c}'\n")

    # 3. Gabungkan klip, pasang audio, dan tempel subtitle
    print("Menggabungkan klip dan menempelkan teks subtitle...")
    
    # Koma dilindungi dengan \\, agar tidak dianggap pemisah filter oleh FFmpeg
    sub_filter = (
        "subtitles=narasi.srt:force_style='Alignment=2\\,"
        "FontSize=22\\,"
        "Bold=1\\,"
        "PrimaryColour=&H00FFFFFF\\,"
        "OutlineColour=&H00000000\\,"
        "BorderStyle=1\\,"
        "Outline=2\\,"
        "MarginV=140'"
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
    # cwd=BASE_DIR memastikan FFmpeg mencari file narasi.srt tepat di folder post_reels
    subprocess.run(cmd_merge, check=True, cwd=BASE_DIR)
    print("Video Reels 30 detik + Subtitle selesai dibuat:", FILE_FINAL)

# ==========================================
# Alur Eksekusi Utama
# ==========================================
def main():
    target = get_species_target()
    print(f"Target Spesies Reels: {target}")

    photos = download_3_photos(target)
    naskah = generate_english_script(target)
    asyncio.run(generate_voiceover_and_subtitles(naskah))
    render_multi_photo_reels(photos)

if __name__ == "__main__":
    main()
