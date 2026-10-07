"""
Bot Mitos vs Fakta - Carousel edukasi flora & fauna Jawa.
Jalan terpisah dari bot utama, tidak mengganggu fitur yang sudah ada.
"""
import os
import json
import time
import random
import requests
from jinja2 import Template
from playwright.sync_api import sync_playwright
from groq import Groq

# --- Kunci rahasia (sama seperti bot utama) ---
GROQ_KEY = str(os.environ.get("GROQ_API_KEY") or "").strip()
FB_PAGE_ID = str(os.environ.get("FB_PAGE_ID") or "").strip()
FB_ACCESS_TOKEN = str(os.environ.get("FB_PAGE_ACCESS_TOKEN") or "").strip()
TELEGRAM_BOT_TOKEN = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

# Jeda acak alami (1-8 menit) agar terlihat organik
jeda = random.randint(60, 480)
print(f"Jeda alami {jeda} detik...")
time.sleep(jeda)

BASE = os.path.dirname(os.path.abspath(__file__))
MITOS_FILE = os.path.join(BASE, "mitos_list.json")
HISTORY_FILE = os.path.join(BASE, "history_mitos.json")
TEMPLATE_FILE = os.path.join(BASE, "template_mitos.html")

def pilih_mitos():
    """Pilih satu mitos yang belum pernah diposting."""
    with open(MITOS_FILE, encoding="utf-8") as f:
        daftar = json.load(f)
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    tersedia = [m for m in daftar if m["spesies"] not in history]
    if not tersedia:
        print("Semua mitos sudah diposting!")
        return None
    return random.choice(tersedia)

def perkaya_fakta(mitos_data):
    """Minta AI Groq untuk memperkaya fakta dengan bahasa menarik."""
    if not GROQ_KEY:
        return mitos_data["fakta_singkat"]
    try:
        client = Groq(api_key=GROQ_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": f"Tulis penjelasan fakta ilmiah yang menarik dalam Bahasa Indonesia (maksimal 3 kalimat pendek) tentang: {mitos_data['spesies']}. Fakta dasar: {mitos_data['fakta_singkat']}. Buat bahasa yang mudah dipahami orang awam dan ada unsur kagumnya."
            }],
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"Groq gagal, pakai fakta bawaan: {e}")
        return mitos_data["fakta_singkat"]

def buat_slide(template, data_slide, output_path):
    """Render satu slide HTML menjadi PNG."""
    html = template.render(**data_slide)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1080})
        page.set_content(html)
        page.screenshot(path=output_path)
        browser.close()
    print(f"Slide tersimpan: {output_path}")

def dapat_ig_user_id():
    """Ambil ID akun Instagram Bisnis yang tertaut ke Halaman Facebook."""
    url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}?fields=instagram_business_account&access_token={FB_ACCESS_TOKEN}"
    data = requests.get(url, timeout=10).json()
    akun = data.get("instagram_business_account")
    if not akun:
        print("Peringatan: tidak ada akun Instagram Bisnis yang tertaut.")
        return None
    return akun["id"]

def upload_ke_facebook(path_gambar):
    """
    Unggah satu gambar ke Halaman Facebook sebagai UNPUBLISHED (tidak tayang di feed),
    kembalikan (photo_id, URL publiknya).

    Kenapa unpublished: upload biasa otomatis jadi 1 postingan sendiri per gambar.
    Dengan unpublished, kita dapat URL-nya tanpa spam feed, lalu keempat foto
    digabung jadi SATU postingan multi-foto lewat attached_media.
    """
    url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/photos"
    with open(path_gambar, "rb") as f:
        res = requests.post(url, data={"published": "false", "access_token": FB_ACCESS_TOKEN},
                            files={"source": f}, timeout=30).json()
    if "id" not in res:
        print(f"Gagal upload ke Facebook: {res}")
        return None, None
    photo_id = res["id"]
    # Ambil URL publik dari foto yang baru diupload
    url_foto = f"https://graph.facebook.com/v21.0/{photo_id}?fields=images&access_token={FB_ACCESS_TOKEN}"
    images = requests.get(url_foto, timeout=10).json().get("images", [])
    if not images:
        return None, None
    return photo_id, images[0]["source"]

def posting_multi_foto_fb(photo_ids, caption):
    """
    Buat SATU postingan Facebook berisi semua foto (album/multi-foto),
    bukan 4 postingan terpisah.
    """
    url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/feed"
    attached = [{"media_fbid": pid} for pid in photo_ids]
    res = requests.post(url, data={
        "message": caption,
        "attached_media": json.dumps(attached),
        "access_token": FB_ACCESS_TOKEN,
    }, timeout=30).json()
    if "id" not in res:
        print(f"Gagal posting multi-foto FB: {res}")
        return None
    print(f"Postingan FB multi-foto tayang! ID: {res['id']}")
    return res["id"]

def posting_carousel_ig(slide_paths, caption):
    """
    Posting 4 slide sebagai satu carousel Instagram.
    Alur: tiap slide jadi 'wadah' dulu -> gabung jadi carousel -> publish.
    Ini aturan resmi Meta API, tidak bisa langsung kirim 4 gambar sekaligus.
    """
    ig_user_id = dapat_ig_user_id()
    if not ig_user_id:
        return None

    # Langkah 1: tiap slide diupload ke Facebook sebagai UNPUBLISHED
    # (tidak tayang di feed) biar dapat URL publik, lalu dibuatkan
    # wadah carousel-item di Instagram
    wadah_ids = []
    fb_photo_ids = []
    for i, path in enumerate(slide_paths, 1):
        print(f"Upload slide {i}/{len(slide_paths)}...")
        photo_id, url_publik = upload_ke_facebook(path)
        if not url_publik:
            print(f"Slide {i} gagal diupload, batalkan carousel.")
            return None
        fb_photo_ids.append(photo_id)
        # Buat wadah item carousel (tanpa caption, caption hanya di induk)
        res = requests.post(
            f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
            data={"image_url": url_publik, "is_carousel_item": "true",
                  "access_token": FB_ACCESS_TOKEN},
            timeout=15).json()
        if "id" not in res:
            print(f"Gagal buat wadah slide {i}: {res}")
            return None
        wadah_ids.append(res["id"])

    # Langkah 1b: posting SATU postingan multi-foto ke Facebook
    # (bukan 4 postingan terpisah seperti sebelumnya)
    print("Posting multi-foto ke Facebook...")
    posting_multi_foto_fb(fb_photo_ids, caption)

    print("Menunggu Instagram memproses semua slide...")
    time.sleep(10)

    # Langkah 2: gabungkan wadah-wadah jadi satu carousel
    res_carousel = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
        data={"media_type": "CAROUSEL",
              "children": ",".join(wadah_ids),
              "caption": caption,
              "access_token": FB_ACCESS_TOKEN},
        timeout=15).json()
    carousel_id = res_carousel.get("id")
    if not carousel_id:
        print(f"Gagal buat carousel: {res_carousel}")
        return None

    time.sleep(5)

    # Langkah 3: publish carousel
    res_publish = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
        data={"creation_id": carousel_id, "access_token": FB_ACCESS_TOKEN},
        timeout=15).json()
    post_id = res_publish.get("id")
    if post_id:
        print(f"CAROUSEL TAYANG! ID: {post_id}")
    else:
        print(f"Gagal publish carousel: {res_publish}")
    return post_id

def kirim_notif_telegram(pesan):
    """Kirim notifikasi ke Telegram (pola sama seperti bot utama)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": pesan,
                                 "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Gagal kirim Telegram: {e}")

def main():
    # Fungsi utama bot. Urutan kerja: pilih mitos -> bikin 4 slide ->
    # posting carousel -> catat history -> lapor ke Telegram.
    mitos = pilih_mitos()
    if not mitos:
        return

    print(f"Mitos terpilih: {mitos['nama_lokal']}")

    # Perkaya fakta dengan AI
    fakta = perkaya_fakta(mitos)

    # Load template
    with open(TEMPLATE_FILE, encoding="utf-8") as f:
        template = Template(f.read())

    # Buat 4 slide
    slides = []
    data_slides = [
        {"tipe": "cover", "nomor": "1", **mitos},
        {"tipe": "mitos", "nomor": "2", **mitos},
        {"tipe": "fakta", "nomor": "3", "fakta": fakta, **mitos},
        {"tipe": "penutup", "nomor": "4", **mitos},
    ]
    for i, ds in enumerate(data_slides, 1):
        path = os.path.join(BASE, f"slide_{i}.png")
        buat_slide(template, ds, path)
        slides.append(path)

    # Caption
    caption = (
        f"🔍 MITOS vs FAKTA: {mitos['nama_lokal']}\n\n"
        f"❌ Mitos: {mitos['mitos']}\n\n"
        f"✅ Fakta: {fakta}\n\n"
        f"Jangan mudah percaya mitos ya! Follow untuk fakta satwa lainnya 🐾\n"
        f"#MitosVsFakta #SatwaJawa #EdukasiSatwa #FloraFaunaIndonesia"
    )

    # Posting carousel ke Instagram
    print("Posting carousel ke Instagram...")
    post_id = posting_carousel_ig(slides, caption)

    if not post_id:
        kirim_notif_telegram(f"❌ *Carousel Mitos vs Fakta gagal diposting!*\n\nTarget: {mitos['nama_lokal']}")
        print("Posting gagal, history tidak dicatat.")
        return

    # Simpan ke history hanya jika posting berhasil
    # (supaya mitos yang gagal tetap bisa dicoba lagi besok)
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    history.append(mitos["spesies"])
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    kirim_notif_telegram(
        f"✅ *Carousel Mitos vs Fakta tayang!*\n\n"
        f"📌 Topik: {mitos['nama_lokal']}\n"
        f"📸 Instagram: `{post_id}`"
    )
    print("Selesai! Mitos vs Fakta berhasil diposting.")

if __name__ == "__main__":
    main()
