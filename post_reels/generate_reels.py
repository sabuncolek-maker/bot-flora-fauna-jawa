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
FILE_HISTORY = os.path.join(BASE_DIR, "history_reels.json")

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
# 1. Manajemen Riwayat Anti-Duplikasi
# ==========================================
def load_history():
    """Membaca riwayat spesies yang sudah pernah diposting"""
    if os.path.exists(FILE_HISTORY):
        try:
            with open(FILE_HISTORY, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_to_history(species_name):
    """Menyimpan spesies ke catatan riwayat agar tidak dobel"""
    history = load_history()
    if species_name not in history:
        history.append(species_name)
    if len(history) > 60:
        history = history[-60:]
    with open(FILE_HISTORY, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

# ==========================================
# 2. Target Spesies Liar Jawa (GBIF API)
# ==========================================
FILE_BINTANG = os.path.join(BASE_DIR, "spesies_bintang.json")

def load_spesies_bintang():
    """Baca daftar spesies bintang yang sudah dikurasi manual.

    KENAPA ada file ini: GBIF mengembalikan spesies acak tanpa filter
    "menarik". Daftar ini berisi ~20 spesies Jawa yang sudah diverifikasi
    asli Jawa dan punya fakta superlatif (terbesar/terkecil/paling langka).
    """
    try:
        with open(FILE_BINTANG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Gagal baca spesies_bintang.json: {e}")
        return []

def get_species_target():
    history = load_history()
    bintang = load_spesies_bintang()
    bintang_tersedia = [s for s in bintang if s["latin"] not in history]

    # 70% pilih dari daftar bintang (sudah kurasi, faktanya akurat),
    # 30% random dari GBIF (variasi). KENAPA: daftar bintang menjamin
    # kualitas & akurasi, GBIF memberi kejutan spesies baru.
    if bintang_tersedia and random.random() < 0.7:
        pilih = random.choice(bintang_tersedia)
        print(f"Target dari daftar bintang: {pilih['latin']} ({pilih['indonesia']})")
        return pilih["latin"]

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
        kandidat = [s for s in semua_spesies if s not in history]
    except Exception as e:
        print(f"Kendala GBIF: {e}")

    if kandidat:
        return random.choice(kandidat)

    # Cadangan: pakai daftar bintang yang belum dipakai
    if bintang_tersedia:
        return random.choice(bintang_tersedia)["latin"]
    # Terakhir: semua bintang (reset siklus)
    if bintang:
        return random.choice(bintang)["latin"]
    return "Panthera pardus melas"

# ==========================================
# 3. Ambil 6 Foto Alam Liar (iNaturalist & Wiki)
# ==========================================
def download_6_photos(scientific_name):
    urls = []
    try:
        url_inat = f"https://api.inaturalist.org/v1/observations?taxon_name={requests.utils.quote(scientific_name)}&has[]=photos&quality_grade=research&license=cc0,cc-by&per_page=15"
        res_inat = requests.get(url_inat, headers=HEADERS_BROWSER, timeout=10).json()
        for item in res_inat.get("results", []):
            for photo in item.get("photos", []):
                link = photo.get("url", "").replace("square", "large")
                if link and link not in urls:
                    urls.append(link)
                if len(urls) >= 6:
                    break
            if len(urls) >= 6:
                break
    except Exception as e:
        print(f"Kendala iNaturalist: {e}")

    if len(urls) < 6:
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
        "https://images.unsplash.com/photo-1470071459604-3b5ec3a7fe05?w=1080",
        "https://images.unsplash.com/photo-1425934398893-310a00990186?w=1080",
        "https://images.unsplash.com/photo-1501854140801-50d01698950b?w=1080",
        "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=1080"
    ]
    for c in cadangan:
        if len(urls) >= 6:
            break
        if c not in urls:
            urls.append(c)

    saved_files = []
    for idx, img_url in enumerate(urls[:6], start=1):
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
# 4. Naskah Narasi Dokumenter (Groq AI)
# ==========================================
def generate_naskah_indonesia(scientific_name, nama_indonesia=""):
    """Narasi dokumenter satwa dalam Bahasa Indonesia yang santai tapi informatif."""
    client = Groq(api_key=GROQ_KEY)
    # Kalau ada fakta singkat dari daftar bintang, sertakan sebagai panduan akurasi
    fakta_panduan = ""
    for s in load_spesies_bintang():
        if s["latin"].lower() == scientific_name.lower():
            fakta_panduan = f"Fakta yang HARUS akurat: {s['fakta_singkat']} "
            break
    prompt = f"""
    Tulis narasi dokumenter satwa liar dalam Bahasa Indonesia tentang '{scientific_name}' ({nama_indonesia}) dari Pulau Jawa.
    {fakta_panduan}
    Gaya: dokumenter alam yang santai dan ramah, seperti bercerita ke teman. Jangan kaku seperti buku teks.
    ATURAN KERAS akurasi: DILARANG menyebut habitat atau latar spesifik yang tidak terverifikasi (jangan tulis "hutan lebat", "lereng berkabut", "kanopi hutan", "rawa", "puncak gunung" atau sejenisnya). Footage hanya foto biasa yang tidak menunjukkan habitat spesifik. Fokus hanya pada: apa spesiesnya, fakta uniknya, perilakunya, dan kenapa ia istimewa. Narasi harus tetap benar walau footage-nya hanya padang rumput biasa.
    FORMAT KHUSUS untuk subtitle modern: pecah narasi menjadi 5-6 SEGMEN pendek.
    - Tiap segmen: maksimal 2 baris, tiap baris maksimal 4 kata
    - Tulis HURUF KAPITAL semua
    - Tandai kata kunci penting (angka, nama, sifat unik) dengan *bintang* di kedua sisinya
    - Pisahkan tiap segmen dengan satu baris kosong
    Contoh format yang benar:
    BANGAU TONGTONG
    ASLI *PULAU JAWA*

    SETINGGI *SATU METER*
    PARUH BESAR KUAT

    *MAKAN BANGKAI*
    JAGA ALAM BERSIH
    Kembalikan HANYA teks narasi ter-segmentasi. Tanpa markdown, tanpa judul, tanpa nomor.
    """
    completion = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5
    )
    naskah = completion.choices[0].message.content.strip().replace('"', '')
    print(f"Naskah Narasi:\n{naskah}\n")
    return naskah

def generate_hook_text(scientific_name, nama_indonesia=""):
    """
    Membuat teks hook (pancingan) untuk 3 detik pertama video.
    Apa itu: 2-4 kata provokatif HURUF BESAR yang muncul besar di tengah layar.
    Kenapa: penonton memutuskan lanjut nonton atau scroll dalam 3 detik
    pertama. Hook yang kuat menaikkan retensi video secara signifikan.
    Bahasa: Indonesia (audiens FB/IG Indonesia).
    """
    # Kalau spesies ada di daftar bintang, pakai fakta_hook yang sudah kurasi
    # (lebih akurat daripada minta LLM mengarang).
    for s in load_spesies_bintang():
        if s["latin"].lower() == scientific_name.lower():
            hook = s["fakta_hook"]
            print(f"Hook (dari daftar bintang): {hook}")
            return hook
    try:
        client = Groq(api_key=GROQ_KEY)
        prompt = f"""
        Buatkan SATU teks hook pendek dalam Bahasa Indonesia untuk video tentang '{scientific_name}' ({nama_indonesia}) dari Pulau Jawa.
        Aturan: MAKSIMAL 4 kata, HURUF BESAR semua, provokatif, bikin penasaran.
        Contoh yang bagus: "BUNGA TERBESAR", "RACUN MEMATIKAN", "HANTU HUTAN JAWA", "TERKECIL DI DUNIA"
        Jawab HANYA teks hook-nya, tanpa penjelasan.
        """
        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=30,
        )
        hook = completion.choices[0].message.content.strip().replace('"', '').upper()
        print(f"Hook: {hook}")
        return hook
    except Exception as e:
        print(f"Gagal buat hook, pakai bawaan: {e}")
        return "SATWA LANGKA JAWA"

# ==========================================
# 5. Audio & Subtitle Per Kalimat (Edge-TTS)
# ==========================================
def format_srt_time(seconds):
    millis = int((seconds - int(seconds)) * 1000)
    secs = int(seconds) % 60
    mins = int(seconds // 60) % 60
    hours = int(seconds // 3600)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

async def create_audio_and_clean_subtitles(text):
    """
    Membuat audio TTS dan subtitle gaya modern (TikTok/Reels) yang SINKRON
    dengan suara narator.

    SINKRONISASI (wajib, bukan opsional):
    - Audio di-generate via edge-tts stream() yang menghasilkan WordBoundary
      (timestamp tiap kata dalam 100-nanosecond ticks)
    - Tiap segmen subtitle dipetakan ke kata-katanya -> timing diambil dari
      timestamp aktual narator, BUKAN tebakan proporsional
    - Fallback berlapis jika WordBoundary tidak tersedia:
      1) ukur durasi audio aktual via ffprobe, bagi proporsional per kata
      2) terakhir: estimasi 26 detik (seperti sebelumnya)

    Format input dari Groq: narasi ter-segmentasi, tiap segmen max 2 baris,
    kata kunci ditandai *bintang*.
    - TTS: teks bersih (tanpa *bintang*) agar dibaca natural
    - SRT: *kata kunci* diubah jadi tag warna kuning ASS {\\c&H00D7FF&}
    - Posisi subtitle: 58% dari atas layar (tidak menutupi hewan di tengah)
    - Font subtitle (26) LEBIH KECIL dari hook (36) -> hierarki visual jelas
    """
    voice = "id-ID-GadisNeural"  # Bahasa Indonesia, suara wanita yang tenang (permintaan Indra)

    # --- Parse segmen dari format Groq ---
    text_bersih = re.sub(r'```[a-z]*\n?', '', text).replace('```', '').strip()
    blok_mentah = [b.strip() for b in re.split(r'\n\s*\n', text_bersih) if b.strip()]

    segmen_list = []
    for blok in blok_mentah:
        baris = [br.strip().upper() for br in blok.split('\n') if br.strip()]
        if baris:
            segmen_list.append(baris[:2])

    if not segmen_list:
        kalimat_list = [k.strip().upper() for k in re.split(r'(?<=[.!?])\s+', text_bersih) if k.strip()]
        segmen_list = [[k] for k in kalimat_list] if kalimat_list else [[text_bersih.upper()]]

    # --- Teks untuk TTS: hapus *bintang*, normalisasi kapitalisasi ---
    def normalisasi_kata(w):
        return re.sub(r'[^a-zA-Z0-9]', '', w).lower()

    teks_tts = re.sub(r'\*([^*]+)\*', r'\1', '\n'.join(' '.join(s) for s in segmen_list))
    teks_tts = teks_tts.capitalize()

    # Kata per segmen (bersih, untuk pemetaan ke WordBoundary)
    kata_per_segmen = []
    for segmen in segmen_list:
        kata = [normalisasi_kata(w) for w in ' '.join(segmen).replace('*', '').split()]
        kata_per_segmen.append([k for k in kata if k])

    # --- Generate audio via stream + kumpulkan WordBoundary ---
    tts = edge_tts.Communicate(teks_tts, voice)
    word_bounds = []  # [(kata_normalisasi, start_detik, end_detik)]
    audio_chunks = []
    try:
        async for chunk in tts.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset & duration dalam 100-nanosecond ticks -> detik
                wb_start = chunk["offset"] / 10_000_000
                wb_end = wb_start + (chunk["duration"] / 10_000_000)
                wb_kata = normalisasi_kata(chunk.get("text", ""))
                if wb_kata:
                    word_bounds.append((wb_kata, wb_start, wb_end))
        with open(FILE_AUDIO, "wb") as f_audio:
            for ch in audio_chunks:
                f_audio.write(ch)
        print(f"TTS selesai: {len(word_bounds)} WordBoundary terkumpul")
    except Exception as e:
        print(f"Stream TTS gagal ({e}), fallback ke save biasa")
        await edge_tts.Communicate(teks_tts, voice).save(FILE_AUDIO)
        word_bounds = []

    # --- Hitung timing tiap segmen ---
    # Metode 1 (ideal): petakan kata segmen ke WordBoundary secara berurutan
    segmen_timing = []  # [(start, end)]
    if word_bounds:
        idx_wb = 0
        for kata_seg in kata_per_segmen:
            t_start, t_end = None, None
            for k in kata_seg:
                # Cari kata yang cocok mulai dari posisi terakhir (berurutan)
                found = False
                for j in range(idx_wb, len(word_bounds)):
                    if word_bounds[j][0] == k:
                        if t_start is None:
                            t_start = word_bounds[j][1]
                        t_end = word_bounds[j][2]
                        idx_wb = j + 1
                        found = True
                        break
                if not found:
                    # Kata tidak ketemu (beda tokenisasi) -> lewati, pakai estimasi
                    break
            if t_start is not None and t_end is not None:
                segmen_timing.append((t_start, t_end))
            else:
                segmen_timing.append((None, None))
        # Isi yang gagal dipetakan dengan interpolasi dari tetangga
        for i in range(len(segmen_timing)):
            if segmen_timing[i][0] is None:
                # Cari tetangga terdekat yang valid
                prev_end = 3.0
                for j in range(i - 1, -1, -1):
                    if segmen_timing[j][0] is not None:
                        prev_end = segmen_timing[j][1]
                        break
                next_start = None
                for j in range(i + 1, len(segmen_timing)):
                    if segmen_timing[j][0] is not None:
                        next_start = segmen_timing[j][0]
                        break
                if next_start is None:
                    # Estimasi: 2 detik per segmen yang tersisa
                    next_start = prev_end + 2.0
                segmen_timing[i] = (prev_end, min(next_start, prev_end + 3.0))

    # Metode 2 (fallback): ukur durasi audio aktual via ffprobe, bagi proporsional
    if not segmen_timing or not word_bounds:
        durasi_audio = 0
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", FILE_AUDIO],
                capture_output=True, text=True, timeout=10
            )
            durasi_audio = float(r.stdout.strip())
            print(f"Durasi audio aktual (ffprobe): {durasi_audio:.1f}s")
        except Exception as e:
            print(f"ffprobe gagal ({e}), pakai estimasi 26s")
            durasi_audio = 26.0
        total_kata = sum(len(ks) for ks in kata_per_segmen) or 1
        waktu_mulai = 3.0
        segmen_timing = []
        for kata_seg in kata_per_segmen:
            dur = max(1.5, (len(kata_seg) / total_kata) * durasi_audio)
            segmen_timing.append((waktu_mulai, waktu_mulai + dur))
            waktu_mulai += dur

    # --- Bangun SRT dengan tag warna kuning + timing sinkron ---
    # Geser semua timing +3.0 detik agar mulai SETELAH hook text (hindari overlap).
    # Tapi jangan melebihi durasi video (~30 detik).
    OFFSET_HOOK = 3.0

    def warnai_kuning(baris_teks):
        # Pakai lambda: hindari masalah escape \1 di re.sub replacement string.
        # Output: {\c&H00D7FF&}kata kunci{\c} (format ASS yang benar)
        return re.sub(r'\*([^*]+)\*',
                      lambda m: '{\\c&H00D7FF&}' + m.group(1) + '{\\c}',
                      baris_teks)

    srt_lines = []
    for i, (segmen, (t0, t1)) in enumerate(zip(segmen_list, segmen_timing), start=1):
        # Jika pakai WordBoundary (timing sudah aktual), tambahkan offset hook
        # Jika fallback proporsional, timing sudah dimulai dari 3.0
        if word_bounds:
            t0, t1 = t0 + OFFSET_HOOK, t1 + OFFSET_HOOK
        # Beri jeda kecil antar segmen agar tidak menumpuk (50ms gap)
        t1_tampil = t1
        baris_srt = '\\N'.join(warnai_kuning(b) for b in segmen)
        srt_lines.append(f"{i}\n{format_srt_time(t0)} --> {format_srt_time(t1_tampil)}\n{baris_srt}\n")

    with open(FILE_SRT, "w", encoding="utf-8") as f_sub:
        f_sub.write("\n".join(srt_lines))
    metode = "WordBoundary (sinkron presisi)" if word_bounds else "proporsional (fallback)"
    print(f"Audio MP3 dan Subtitle modern selesai! ({len(segmen_list)} segmen, metode: {metode})")

# ==========================================
# 6. Render Video 6 Foto & Hardsub (FFmpeg)
# ==========================================
FILE_HOOK_SRT = os.path.join(BASE_DIR, "hook.srt")

def render_multi_photo_reels(photo_files, hook_text=""):
    """
    Render reels:
    1. BACKGROUND BLUR - gambar tampil UTUH (fit) di atas background blur,
       tidak dipotong seperti sebelumnya
    2. GERAKAN HALUS - zoom sangat perlahan (di-upscale 2x dulu agar
       tidak geter), 2 variasi: zoom masuk / zoom keluar
    3. HARD CUT - antar foto potongan langsung tanpa efek
       (fade xfade menimbulkan ghosting/frame hantu, tidak cocok untuk reels)
    4. DURASI BERVARIASI - tiap foto 3-6 detik acak
    5. MUSIK LATAR ALAM - brown noise lembut di bawah narasi
    6. HOOK TEXT - teks besar (font 36) di tengah layar selama 3 detik pertama
       (Bahasa Indonesia, 2-4 kata, provokatif)
    7. SUBTITLE MODERN - gaya TikTok/Reels: font 26 bold kapital (LEBIH KECIL
       dari hook utk hierarki visual), outline hitam tebal, posisi 58% dari
       atas layar (tidak menutupi hewan), kata kunci berwarna kuning,
       segmen pendek 2 baris yang ganti mengikuti narasi
    """
    # Tulis SRT hook: tampil 0.5 - 3.0 detik, font BESAR di tengah
    hook_filter = ""
    if hook_text and hook_text.strip():
        with open(FILE_HOOK_SRT, "w", encoding="utf-8") as f_hook:
            f_hook.write("1\n00:00:00,500 --> 00:00:03,000\n" + hook_text.strip() + "\n")
        hook_filter = (
            "subtitles=hook.srt:force_style='Alignment=5\\,"
            "FontSize=36\\,"
            "Bold=1\\,"
            "PrimaryColour=&H00FFFFFF\\,"
            "OutlineColour=&H00000000\\,"
            "BorderStyle=1\\,"
            "Outline=2\\,"
            "MarginV=0'"
        )
        print(f"Hook overlay aktif: {hook_text.strip()}")
    # Pengaman: butuh minimal 2 foto agar reels tidak terlalu pendek
    if len(photo_files) < 2:
        raise RuntimeError(
            f"Butuh minimal 2 foto untuk render reels, hanya dapat {len(photo_files)}. "
            "Kemungkinan download foto gagal (jaringan/API sumber foto bermasalah)."
        )
    # --- Gerakan kamera: hanya 2, sangat halus ---
    # 'on' = nomor frame output (0 sampai d-1)
    # Trik anti-geter: gambar di-upscale 2x DULU sebelum zoompan,
    # sehingga langkah zoom-nya jauh lebih halus
    GERAKAN_KAMERA = [
        # Zoom masuk sangat perlahan (1.0 -> ~1.09)
        "z='1+0.0006*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        # Zoom keluar sangat perlahan (~1.09 -> 1.0)
        "z='max(1.09-0.0006*on\\,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    ]

    FPS = 25

    cmd = ["ffmpeg", "-y"]
    filter_parts = []
    durasi_list = []

    # --- Langkah 1: tiap foto jadi klip dengan gerakan acak ---
    # PENTING: input adalah 1 gambar statis (tanpa -loop, tanpa -t).
    # zoompan d=N membuat tepat N frame dari 1 gambar tersebut.
    # (Bug sebelumnya: pakai -loop 1 sehingga tiap frame input
    #  di-zoom N kali -> video jadi 10 menit!)
    for idx, photo in enumerate(photo_files):
        dur = random.choice([3, 4, 5, 6])  # durasi acak 3-6 detik
        durasi_list.append(dur)
        gerakan = random.choice(GERAKAN_KAMERA)
        frames = dur * FPS

        cmd += ["-i", photo]

        # Background: isi penuh frame + blur
        # Foreground: tampil UTUH (decrease = fit, tidak dipotong)
        # Lalu composite di-upscale 2x sebelum zoompan agar gerakan halus
        filter_parts.append(
            f"[{idx}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,boxblur=15:3[bg{idx}];"
            f"[{idx}:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg{idx}];"
            f"[bg{idx}][fg{idx}]overlay=(W-w)/2:(H-h)/2,"
            f"scale=2160:3840,"
            f"zoompan={gerakan}:d={frames}:s=1080x1920:fps={FPS},"
            f"settb=AVTB[v{idx}]"
        )
        print(f"  Foto {idx+1}: durasi {dur} detik, gerakan acak")

    # --- Langkah 2: gabung semua klip dengan HARD CUT (concat) ---
    # xfade fade sebelumnya menimbulkan ghosting (frame hantu/ganda) saat
    # transisi karena dua klip zoom yang berbeda di-overlay. Hard cut via
    # concat lebih bersih dan ritmenya lebih cocok untuk reels.
    label_input = "".join(f"[v{i}]" for i in range(len(photo_files)))
    filter_parts.append(
        f"{label_input}concat=n={len(photo_files)}:v=1:a=0[vconcat]"
    )
    label_akhir = "[vconcat]"

    # --- Langkah 3: hook overlay + subtitle + musik latar + render final ---\n
    # SUBTITLE MODERN (gaya TikTok/Reels viral):
    # - Alignment=8 (top-center) + MarginV=1080 -> teks di 58% dari atas layar
    #   (video 1920px tinggi; 58% = ~1114px; teks 2 baris ~70px -> MarginV 1080
    #   menaruh TENGAH blok teks tepat di 58%. TIDAK menutupi hewan di tengah.)
    # - FontSize=26: LEBIH KECIL dari hook (36) -> hierarki visual jelas
    #   (hook = penarik perhatian, subtitle = pendukung)
    # - Outline=4: outline hitam TEBAL agar terbaca di atas video apapun
    # - Kata kunci kuning via tag ASS {\\c&H00D7FF&} di file SRT
    sub_filter = (
        "subtitles=narasi.srt:force_style='Alignment=8\\,"
        "FontSize=26\\,"
        "Bold=1\\,"
        "PrimaryColour=&H00FFFFFF\\,"
        "OutlineColour=&H00000000\\,"
        "BorderStyle=1\\,"
        "Outline=4\\,"
        "MarginV=1080'"
    )

    # Musik latar: brown noise (suara dengung rendah seperti angin)
    # difilter lowpass agar halus, volume 0.10 (terdengar lembut tapi
    # tidak mengganggu narasi). Dibuat langsung oleh ffmpeg -
    # tidak butuh file eksternal, bebas masalah hak cipta.
    ambient_filter = (
        "anoisesrc=color=brown:duration=40:sample_rate=44100[noise];"
        "[noise]lowpass=f=400,volume=0.10[amb]"
    )

    # Rantai video: klip -> hook overlay (jika ada) -> subtitle narasi
    rantai_video = f"{label_akhir}"
    if hook_filter:
        rantai_video += f"{hook_filter}[vhook];[vhook]"
    rantai_video += f"{sub_filter}[vout]"
    full_filter = (
        ";".join(filter_parts) + ";" + rantai_video + ";"
        + ambient_filter + ";[aud_in][amb]amix=inputs=2:duration=first[aout]"
    )

    idx_audio = len(photo_files)
    cmd += ["-i", FILE_AUDIO]
    # Beri label pada audio input agar bisa dirujuk di filter
    full_filter = full_filter.replace("[aud_in]", f"[{idx_audio}:a]")
    cmd += [
        "-filter_complex", full_filter,
        "-map", "[vout]",
        "-map", "[aout]",  # audio campuran: narasi + musik latar
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        FILE_FINAL
    ]

    print("Merender video final (hard cut, tanpa transisi)...")
    subprocess.run(cmd, check=True, cwd=BASE_DIR)
    print("Render final Reels sukses:", FILE_FINAL)

# ==========================================
# 7. Distribusi Telegram Bot
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
# 8. Deteksi ID Instagram Bisnis
# ==========================================
def get_instagram_id():
    try:
        url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}?fields=instagram_business_account&access_token={FB_TOKEN}"
        res = requests.get(url, timeout=10).json()
        ig_id = res.get("instagram_business_account", {}).get("id")
        return ig_id
    except Exception as e:
        print(f"Kendala mencari Instagram ID: {e}")
        return None

# ==========================================
# 9. Publikasi Instagram Reels
# ==========================================
def post_instagram_reels(video_path, caption_text, ig_id):
    """
    Posting reels ke Instagram. Mengembalikan True jika sukses, False jika gagal.
    History hanya dicatat jika posting berhasil (lihat main()).
    """
    if not ig_id:
        print("ID Instagram tidak ditemukan. Melewati posting Instagram.")
        return True  # bukan kegagalan, hanya dilewati

    try:
        print(f"Menginisialisasi Instagram Reels (IG ID: {ig_id})...")
        init_url = f"https://graph.facebook.com/v21.0/{ig_id}/media"
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
            return False

        with open(video_path, "rb") as f:
            video_data = f.read()

        headers = {
            "Authorization": f"OAuth {FB_TOKEN}",
            "offset": "0",
            "file_size": str(len(video_data))
        }
        requests.post(upload_uri, headers=headers, data=video_data, timeout=120)

        print("Menunggu proses render di server Meta...")
        status_url = f"https://graph.facebook.com/v21.0/{video_id}?fields=status_code&access_token={FB_TOKEN}"
        for _ in range(12):
            time.sleep(10)
            status_res = requests.get(status_url, timeout=10).json()
            if status_res.get("status_code") == "FINISHED":
                break

        pub_url = f"https://graph.facebook.com/v21.0/{ig_id}/media_publish"
        pub_res = requests.post(pub_url, data={"creation_id": video_id, "access_token": FB_TOKEN}, timeout=20).json()
        if pub_res.get("id"):
            print("-> SUKSES! Instagram Reels terbit. ID:", pub_res.get("id"))
            return True
        print(f"Gagal publish IG Reels: {pub_res}")
        return False
    except Exception as e:
        print(f"Kendala posting Instagram Reels: {e}")
        return False

# ==========================================
# 10. Publikasi Facebook Reels
# ==========================================
def post_facebook_reels(video_path, caption_text):
    """
    Posting reels ke Facebook. Mengembalikan True jika sukses, False jika gagal.
    History hanya dicatat jika posting berhasil (lihat main()).
    """
    if not (FB_TOKEN and FB_PAGE_ID):
        print("Kredensial Facebook belum lengkap, lewati posting FB.")
        return True  # bukan kegagalan, hanya dilewati

    try:
        print("Menginisialisasi Facebook Reels...")
        init_url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/video_reels"
        r_init = requests.post(init_url, data={"upload_phase": "start", "access_token": FB_TOKEN}, timeout=20).json()
        video_id = r_init.get("video_id")
        upload_url = r_init.get("upload_url")

        if not upload_url:
            print(f"Gagal inisialisasi FB Reels: {r_init}")
            return False

        with open(video_path, "rb") as f:
            video_data = f.read()

        headers = {
            "Authorization": f"OAuth {FB_TOKEN}",
            "offset": "0",
            "file_size": str(len(video_data))
        }
        requests.post(upload_url, headers=headers, data=video_data, timeout=120)

        print("Menerbitkan Facebook Reels ke Halaman...")
        publish_url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/video_reels"
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
            return True
        print(f"Respon penerbitan FB Reels: {r_pub}")
        return False
    except Exception as e:
        print(f"Kendala posting Facebook Reels: {e}")
        return False

# ==========================================
# Alur Utama
# ==========================================
def main():
    # Jeda acak singkat (10 detik) agar saat dites manual tidak menunggu lama
    jeda_detik = random.randint(60, 480)
    print(f"Menunggu jeda alami selama {jeda_detik} detik sebelum memproses...")
    time.sleep(jeda_detik)

    target = get_species_target()
    print(f"Target Spesies Reels: {target}")

    # Cari nama Indonesia dari daftar bintang (untuk hook & narasi)
    nama_id = ""
    for s in load_spesies_bintang():
        if s["latin"].lower() == target.lower():
            nama_id = s["indonesia"]
            break

    photos = download_6_photos(target)
    hook = generate_hook_text(target, nama_id)
    naskah = generate_naskah_indonesia(target, nama_id)
    asyncio.run(create_audio_and_clean_subtitles(naskah))
    render_multi_photo_reels(photos, hook)

    # Caption: bersihkan format segmen (hapus *bintang*, gabung jadi paragraf rapi)
    naskah_caption = re.sub(r'\*([^*]+)\*', r'\1', naskah).replace('\n', ' ')
    naskah_caption = re.sub(r'\s+', ' ', naskah_caption).strip()
    caption = (
        f"Satwa liar Jawa: {target}" + (f" ({nama_id})" if nama_id else "") + ".\n\n"
        f"{naskah_caption}\n\n"
        f"#satwajawa #florafauna #indonesia #jawa #wildlife #biodiversity #indobizarre"
    )

    send_to_telegram(FILE_FINAL, caption)
    fb_ok = post_facebook_reels(FILE_FINAL, caption)

    ig_ok = True
    ig_id = get_instagram_id()
    if ig_id:
        ig_ok = post_instagram_reels(FILE_FINAL, caption, ig_id)

    # Catat history HANYA jika semua posting berhasil.
    # Kalau ada yang gagal, spesies tidak dicatat agar bisa dicoba lagi lain waktu.
    if fb_ok and ig_ok:
        save_to_history(target)
        print("Spesies resmi dicatat ke history_reels.json!")
    else:
        print("Posting belum lengkap, history TIDAK dicatat - akan dicoba lagi lain waktu.")

if __name__ == "__main__":
    main()
