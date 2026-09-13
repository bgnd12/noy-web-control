# Noy Web Control

Remote/control panel berbasis web untuk **Noy AI Agent**. Project ini **terpisah total**
dari `noy-agent/` — tidak ada satu baris pun kode Noy yang diubah, dipindah, atau diimpor.
Komunikasi hanya lewat subprocess (menjalankan `py -3.12 main.py` seperti biasa Anda
ketik manual) dan pembacaan stdout-nya.

```
Browser  →  Noy Web Control (FastAPI)  →  subprocess  →  Noy Agent (main.py)
                    ↓
              WebSocket realtime ke browser
```

---

## 1. Struktur folder

```
noy-web-control/
├── backend/
│   ├── main.py             # FastAPI app: REST API + WebSocket + serve frontend
│   ├── agent_manager.py    # Bridge: start/stop/status subprocess Noy, parsing log
│   ├── settings_store.py   # Simpan/ambil pengaturan Web Control (settings.json)
│   ├── config.py           # Baca konfigurasi dari .env
│   ├── requirements.txt
│   ├── .env.example        # Salin -> .env, sesuaikan path Noy Agent Anda
│   └── data/                # (dibuat otomatis) tempat settings.json disimpan
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── testing/
│   └── dummy_noy_agent.py  # Skrip dummy HANYA untuk uji coba, BUKAN Noy asli
└── README.md               # file ini
```

**File Noy lama yang disentuh: TIDAK ADA.** Tidak ada file di `noy-agent/` yang dibuat,
diubah, atau dihapus oleh project ini.

---

## 2. Instalasi

Butuh Python 3.12 (sama seperti Noy Agent Anda) dan bisa dijalankan lewat `py` launcher
di Windows.

```bash
cd noy-web-control/backend
py -3.12 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

> Kalau instalasi `sounddevice` gagal (butuh PortAudio), hapus baris itu dari
> `requirements.txt` dan install ulang — fitur lain tetap jalan normal, hanya
> daftar mikrofon otomatis yang tidak tersedia.

---

## 3. Konfigurasi

```bash
cd backend
copy .env.example .env
```

Buka `.env` dan sesuaikan minimal dua baris ini dengan lokasi Noy Agent Anda:

```env
NOY_AGENT_DIR=C:\Users\NamaAnda\Projects\NOY\noy-agent
NOY_AGENT_CMD=py -3.12 main.py
```

`NOY_AGENT_CMD` sengaja tetap (fixed) dan hanya bisa diubah lewat file `.env` ini —
**tidak ada endpoint di Web Control yang menerima command dari browser.**

---

## 4. Menjalankan Web Control

```bash
cd noy-web-control
py -3.12 -m uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

Lalu buka browser ke:

```
http://127.0.0.1:8787
```

Noy Agent **tidak perlu dijalankan manual lagi** — tekan tombol **Start Noy** di web,
yang di baliknya akan menjalankan `py -3.12 main.py` di folder `NOY_AGENT_DIR` untuk Anda.

---

## 5. Cara kerja START / STOP

- **Start Noy** → memanggil `POST /api/start`. Backend mengecek apakah proses Noy
  sudah berjalan (disimpan sebagai satu `subprocess.Popen` di memori). Jika sudah
  jalan, tombol tidak membuat proses baru — hanya mengembalikan status
  `already_running`. Jika belum, backend menjalankan `NOY_AGENT_CMD` di
  `NOY_AGENT_DIR` dan mulai membaca stdout-nya.
- **Stop Noy** → memanggil `POST /api/stop`. Backend mengirim `terminate()` (setara
  menutup proses secara normal), menunggu hingga `NOY_STOP_GRACE_SECONDS` detik,
  dan baru melakukan `kill()` paksa jika proses tidak merespons dalam waktu itu.
- **Crash detection** — jika Noy Agent berhenti sendiri (misalnya error/crash) tanpa
  melalui tombol Stop, status otomatis berubah ke `OFFLINE` dan tercatat di Activity.

### Status yang ditampilkan

`STOPPED → STARTING → ONLINE/LISTENING/PROCESSING/RESPONDING → STOPPED`
(atau `OFFLINE` jika crash).

Status `LISTENING` / `PROCESSING` / `RESPONDING` disimpulkan dari pola teks pada
stdout Noy (lihat `_STATE_PATTERNS` di `backend/agent_manager.py`), **tanpa mengubah
kode Noy**. Jika baris log asli Noy Anda berbeda kata-katanya dari pola default,
tinggal tambahkan pola regex baru di file tersebut — masih tidak menyentuh
`noy-agent/`.

---

## 6. Endpoint API

| Method | Endpoint          | Fungsi                                              |
|--------|-------------------|------------------------------------------------------|
| GET    | `/api/status`     | Status Noy saat ini + log terakhir                   |
| POST   | `/api/start`      | Jalankan Noy Agent (aman dari duplikasi proses)      |
| POST   | `/api/stop`       | Hentikan Noy Agent secara graceful                   |
| GET    | `/api/settings`   | Ambil pengaturan Web Control                         |
| POST   | `/api/settings`   | Simpan pengaturan Web Control                        |
| GET    | `/api/microphones`| Daftar mikrofon (best-effort, opsional)              |
| WS     | `/ws/status`      | Stream realtime status + activity log ke browser     |

**Tidak ada** endpoint seperti `/api/execute?command=...` atau akses shell bebas.
Satu-satunya command yang bisa dijalankan adalah `NOY_AGENT_CMD` yang tetap.

---

## 7. Settings & batasan tahap ini

Pengaturan (Wake Word/Push-to-Talk, shortcut PTT, STT silence timeout, mikrofon,
volume TTS, debug mode) disimpan di `backend/data/settings.json`, **milik Web
Control sendiri**. Pada tahap ini, Noy Agent belum membaca file tersebut — sesuai
instruksi awal untuk tidak mengubah `noy-agent/` di tahap ini. Artinya:

- Pengaturan sudah bisa diisi, disimpan, dan dilihat kembali dari web.
- Agar pengaturan ini benar-benar mengubah perilaku Noy (mis. Noy membaca
  `stt_silence_timeout_ms` dari file ini), perlu langkah integrasi lanjutan di
  sisi `noy-agent/` — sengaja belum dilakukan di tahap ini sesuai batasan yang
  Anda tetapkan.

Toggle mode Wake Word/Push-to-Talk saat ini juga hanya tersimpan sebagai preferensi
di Web Control (untuk ditampilkan di UI), dengan alasan yang sama.

---

## 8. Cara menguji START tanpa Noy asli (opsional)

Karena saya tidak memiliki akses ke source code `noy-agent/` Anda yang sebenarnya,
project ini disertai `testing/dummy_noy_agent.py` — skrip tiruan yang mencetak log
mirip siklus Noy asli, untuk memverifikasi bridge-nya bekerja.

1. Di `backend/.env`, arahkan sementara:
   ```env
   NOY_AGENT_DIR=<path>\noy-web-control\testing
   NOY_AGENT_CMD=py -3.12 dummy_noy_agent.py
   ```
2. Jalankan Web Control, buka browser, klik **Start Noy**.
3. Anda akan melihat status berpindah `STARTING → ONLINE → LISTENING → PROCESSING
   → RESPONDING` berulang, dan Activity log terisi baris seperti "Halo Noy
   terdeteksi", "Listening...", dst.
4. Klik **Start Noy** lagi berkali-kali → status tetap sama, tidak ada proses baru
   dibuat (cek Task Manager: hanya satu proses `python.exe`/`py.exe` untuk dummy
   agent).
5. Klik **Stop Noy** → proses berhenti graceful, status kembali `STOPPED`.
6. Setelah yakin bridge bekerja, **kembalikan** `NOY_AGENT_DIR`/`NOY_AGENT_CMD` ke
   path `noy-agent/main.py` Anda yang asli.

---

## 9. Hasil pengujian (di sandbox pengembangan, memakai dummy agent)

Karena source `noy-agent/` asli Anda tidak tersedia di sesi ini, pengujian di bawah
dilakukan terhadap `testing/dummy_noy_agent.py` sebagai pengganti sementara, untuk
memvalidasi mekanisme bridge (bukan fitur STT/LLM/TTS Noy itu sendiri — itu tidak
diubah dan tidak perlu diuji ulang).

- **Start (klik 1x):** proses ter-spawn, status `STARTING` → `ONLINE`, PID tercatat.
- **Start (klik berulang saat sudah jalan):** tidak ada proses kedua dibuat; respons
  `already_running`.
- **Siklus log:** status berpindah otomatis mengikuti pola stdout dummy agent
  (`LISTENING` → `PROCESSING` → `RESPONDING`), Activity log ter-update realtime
  lewat WebSocket.
- **Stop (saat berjalan):** proses berhenti dalam grace period, status kembali
  `STOPPED`, log "Noy Agent berhenti." tercatat.
- **Stop (saat sudah berhenti):** tidak error, respons `already_stopped`.
- **Simulasi crash** (proses dihentikan paksa dari luar): status otomatis berubah
  ke `OFFLINE` pada polling/refresh status berikutnya.

## 10. Masalah yang ditemukan / catatan untuk langkah berikutnya

1. **Belum diuji terhadap `noy-agent/` asli** — karena source code-nya tidak
   tersedia di sesi pengembangan ini. Anda perlu menjalankan langkah di bagian
   §8 dengan `NOY_AGENT_CMD` yang mengarah ke `main.py` asli, lalu cek apakah
   pola regex di `_STATE_PATTERNS` (`backend/agent_manager.py`) cocok dengan
   format log asli Noy — jika tidak cocok persis, status akan tetap `ONLINE`
   generik (bukan `LISTENING`/`PROCESSING`/dst), tapi START/STOP/Activity log
   tetap berfungsi normal.
2. **Status Microphone/LLM/TTS** di panel saat ini disederhanakan menjadi
   "Connected saat Noy online" — belum ada sinyal granular per-komponen dari
   Noy Agent (mis. LLM sedang timeout tapi mic tetap aktif). Ini bisa
   ditingkatkan di iterasi berikutnya jika Noy Agent bersedia mencetak baris
   status per-komponen yang bisa di-parse.
3. **Dry Run Mode** Noy tidak disentuh sama sekali oleh Web Control — tetap
   sepenuhnya dikendalikan oleh `noy-agent/` seperti sebelumnya.
4. Di Windows, `subprocess.terminate()` pada proses yang dibungkus `py` launcher
   umumnya bekerja baik, tapi jika Noy Agent Anda membuka child process sendiri
   (mis. untuk audio), pastikan Noy menangani sinyal terminate/interrupt agar
   child process ikut berhenti — ini bagian dari kode Noy, tidak diubah di sini.
