# Bot Flora Fauna Jawa 🌿🐾

Bot otomatis yang membuat dan memposting konten edukasi tentang flora & fauna Pulau Jawa ke **Instagram**, **Facebook**, dan **Telegram**. Berjalan sendiri 100% lewat GitHub Actions — tanpa perlu disentuh.

## 📦 Jenis Konten

| Konten | Format | Jadwal |
|--------|--------|--------|
| 🖼️ Infografis | Gambar 1080×1080 (foto besar + fakta) | 3x sehari |
| 🎬 Reels | Video vertikal ±30 detik (narasi Inggris + subtitle) | 3x sehari (23:37, 02:43, 05:21 WIB) |
| 🔄 Mitos vs Fakta | Carousel 4 slide (cover → mitos → fakta → penutup) | Rabu & Sabtu, 10:17 WIB |

## ⚙️ Cara Kerja

1. **Cari spesies** — Bot mengambil data satwa/tumbuhan Jawa dari GBIF (basis data hayati global), fokus ke yang statusnya terancam punah.
2. **Ambil foto** — Foto berlisensi terbuka (CC0/CC-BY) dari iNaturalist & Wikimedia Commons. Aman dari masalah hak cipta.
3. **Buat konten** —
   - Infografis: template HTML di-render jadi gambar via Playwright
   - Reels: 6 foto digabung jadi video via FFmpeg (background blur, zoom halus, transisi fade, musik latar, subtitle)
   - Carousel: 4 slide HTML di-render jadi gambar
4. **Posting otomatis** — Ke Instagram, Facebook Page, dan notifikasi Telegram via Meta Graph API.
5. **Catat riwayat** — Spesies yang sudah diposting dicatat agar tidak duplikat. History hanya dicatat kalau posting **berhasil**.

## 📂 Struktur Folder

```
├── .github/workflows/
│   ├── daily_post.yml    # Infografis (3x sehari)
│   ├── daily_reels.yml   # Reels (3x sehari)
│   └── mitos_fakta.yml   # Carousel Mitos vs Fakta (2x seminggu)
├── post_infografis/
│   ├── generate_and_post.py  # Script infografis
│   ├── template.html         # Desain infografis
│   └── posted.txt            # Riwayat spesies infografis
├── post_reels/
│   ├── generate_reels.py     # Script reels
│   └── history_reels.json    # Riwayat spesies reels
├── post_mitos_fakta/
│   ├── generate_mitos.py     # Script carousel
│   ├── template_mitos.html   # Desain slide carousel
│   ├── mitos_list.json       # Daftar mitos
│   └── history_mitos.json    # Riwayat mitos
└── requirements.txt
```

## 🛠️ Setup (untuk fork)

Repo ini public agar dapat menit GitHub Actions gratis. Kalau mau fork, isi **GitHub Secrets** berikut:

| Secret | Isi |
|--------|-----|
| `GROQ_API_KEY` | API key Groq (untuk AI narasi & fakta) |
| `FB_PAGE_ID` | ID numerik Halaman Facebook |
| `FB_PAGE_ACCESS_TOKEN` | Token akses Halaman Facebook |
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram |
| `TELEGRAM_CHAT_ID` | ID chat Telegram untuk notifikasi |

## 🧪 Tes Manual

Buka tab **Actions** di GitHub → pilih workflow → **Run workflow** → **Run workflow**.

## 📝 Catatan

- Jadwal pakai zona waktu UTC di file workflow (WIB = UTC+7).
- Jangan push ke `main` saat workflow sedang berjalan — bisa bikin push riwayat ditolak. Workflow sudah dilengkapi `git pull --rebase` sebagai pengaman.
