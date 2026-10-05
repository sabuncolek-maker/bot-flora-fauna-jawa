import os
import sys
import json
import random
import asyncio
import subprocess
import requests
import edge_tts
from groq import Groq

# Mengunci folder kerja otomatis di folder 'post_reels'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_AUDIO = os.path.join(BASE_DIR, "narasi.mp3")
FILE_IMAGE = os.path.join(BASE_DIR, "foto.jpg")
FILE_OUTPUT = os.path.join(BASE_DIR, "reels_30detik.mp4")

GROQ_KEY = str(os.environ.get("GROQ_API_KEY") or "").strip()
if not GROQ_KEY:
    raise ValueError("GROQ_API_KEY belum terpasang di GitHub Secrets!")

# ==========================================
# 1. Ambil Spesies dari GBIF (Titik Jawa)
# ==========================================
def get_species_target():
    """Mengambil spesies liar Jawa dari GBIF API atau fallback cadangan"""
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
        res = requests.get(url, params=params, timeout=12).json()
        results = res.get("results", [])
        kandidat = [item.get("species") for item in results if item.get("species")]
        if kandidat:
            return random.choice(list(set(kandidat)))
    except Exception as e:
        print(f"Kendala GBIF: {e}")
    # Cadangan lokal jika server GBIF sibuk
    return random.choice(["Panthera pardus melas", "Nisaetus bartelsi", "Presbytis comata"])

# ==========================================
# 2. Ambil Foto Asli Resolusi Tinggi
# ==========================================
def download_photo(scientific_name):
    """Mencari foto satwa dari Wikipedia dan menyimpannya sebagai foto.jpg"""
    url_foto = ""
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(scientific_name)}"
        headers = {"User-Agent": "FaunaBot/1.0"}
        r = requests.get(url, headers=headers, timeout=10).json()
        if "originalimage" in r:
            url_foto = r["originalimage"]["source"]
    except Exception:
        pass

    # Jika Wikipedia tidak memiliki foto, gunakan cadangan alam hutan
    if not url_foto:
        url_foto = "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w=1080"

    res_img = requests.get(url_foto, timeout=15)
    with open(FILE_IMAGE, "wb") as f:
        f.write(res_img.content)
    print(f"Foto berhasil diunduh dari: {url_foto}")

# ==========================================
# 3. Riset Naskah 30 Detik (Groq AI)
# ==========================================
def generate_english_script(scientific_name):
    """Membuat naskah narasi bahasa Inggris berdurasi pas 30 detik (65-70 kata)"""
    client = Groq(api_key=GROQ_KEY)
    prompt = f"""
    Write a dramatic, captivating wildlife documentary voiceover script about '{scientific_name}' from Java Island.
    Style: National Geographic or BBC Earth documentary style.
    Length constraint: Exactly between 65 and 70 words (to precisely match a 28-30 seconds speech duration).
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
# 4. Buat Rekaman Suara (Edge-TTS)
# ==========================================
async def generate_voiceover(text):
    """Mengubah teks naskah menjadi file suara narator dokumenter"""
    # Karakter suara narator pria Amerika berwibawa
    voice = "en-US-ChristopherNeural"
    tts = edge_tts.Communicate(text, voice)
    await tts.save(FILE_AUDIO)
    print("Rekaman audio narasi berhasil disimpan ke:", FILE_AUDIO)

# ==========================================
# 5. Render Video Vertikal (Ken Burns via FFmpeg)
# ==========================================
def render_video_30s():
    """Merakit foto dan audio menjadi video Reels vertikal 1080x1920 durasi 30 detik"""
    print("Memulai proses render video dengan FFmpeg...")
    # d=750 frame pada 25fps = 30 detik pas
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", FILE_IMAGE,
        "-i", FILE_AUDIO,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='min(zoom+0.0006,1.25)':d=750:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=25",
        "-c:v", "libx264",
        "-t", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        FILE_OUTPUT
    ]
    subprocess.run(cmd, check=True)
    print("Render berhasil! File video tersimpan di:", FILE_OUTPUT)

# ==========================================
# Eksekusi Utama
# ==========================================
def main():
    target = get_species_target()
    print(f"Target Spesies Reels: {target}")

    download_photo(target)
    naskah = generate_english_script(target)
    asyncio.run(generate_voiceover(naskah))
    render_video_30s()

if __name__ == "__main__":
    main()
