# Mangatan: Setup & Documentation

Mangatan is a language-learning tool that creates Yomitan-scannable text overlays directly on top of manga served by **Suwayomi**.

> ### 💡 Looking for a Simpler Setup?
> For a streamlined application for reading and mining manga, anime, and novels on all platforms, check out: **[Manatan on GitHub](https://github.com/KolbyML/Manatan)**.

#### Demo Showcase

https://github.com/user-attachments/assets/b7649677-3453-4bca-8898-56761141bc31

---

### 📐 How It Works
1. **The Reader:** You open and read manga in your browser using **Suwayomi**.
2. **The Scanner:** The **Tampermonkey Userscript** running in your browser detects when a page loads.
3. **The Process:** The background server downloads the manga image, runs OCR on it, and sends back the text and coordinate data.
4. **The Overlay:** The userscript turns this data into a matching, interactive text layer placed on top of the manga image, making it fully scannable with Yomitan.

---

## ⚙️ Essential Yomitan Configuration
To ensure dictionary lookups parse smoothly across line breaks and mining works correctly, adjust these settings in Yomitan:

1. **Replacement Patterns (under Translation settings):**
   * Add a pattern to replace `\n` with nothing.
   * Add a pattern to replace `\s` with nothing.
2. **Enable Character Search:** 
   * Toggle on **"Search text with non-Japanese, Chinese, or Korean characters"**. This allows Yomitan to correctly look up vocabulary that spans across line breaks containing hidden spaces or formatting characters.
3. **Sentence Termination:**
   * Go to `Text Parsing > Sentence termination characters` and change it to **"Custom, no new lines"** to prevent mined sentences from being cut short at line breaks.

---

## Key Features & Recent Updates

### 🔍 OCR Engine Selection (Background Server)
* **Google Lens (Online):** Cloud-based text recognition leveraging Google's OCR engine. Fast and accurate with no local model setup required, but depends on an active internet connection.
* **Manga-OCR + Meiki Text Detection:** Highly accurate local OCR. Models automatically download on first run.
  * [Meiki Text Detection Model](https://huggingface.co/rtr46/meiki.text.detect.v0)
  * [ONNX Manga-OCR Model (DirectML)](https://huggingface.co/xingliao/manga-ocr-onnx-full)
* **OneOCR with Preprocessing:** Resizes images to 2500px wide, sharpens the source, performs OCR, and applies a Furigana filter to prevent dictionary selection errors.

### 🎴 Advanced Mining & Editing
* **Editable Textboxes:** Double-click any textbox to edit or correct OCR mistakes. Changes sync directly to the background server's local cache.
* **Merging & Deleting:** Hold your designated modifier keys (default: `Ctrl` to merge, `Alt` to delete) or use the mobile FAB menu to adjust box layouts.
* **Anki Image Export:** Includes an active image picker to specify which page gets cropped during dual-page layouts.

### 🎨 Visuals & Controls
* **FAB Menu:** Easily toggle between Edit, Merge, and Delete modes, or quick-trigger the Anki export.

---

## 🛠️ Global Prerequisites

Before configuring your background OCR server, set up the frontend:

1. **Suwayomi-Server**  
   Set up and launch **[Suwayomi-Server](https://github.com/Suwayomi/Suwayomi-Server)**.
2. **Tampermonkey & Userscript**  
   * Install the **[Tampermonkey](https://www.tampermonkey.net/)** browser extension.
   * Go to your browser's extension settings for Tampermonkey and toggle **"Allow access to file URLs"** to enabled.
   * Install the Mangatan **[userscript](https://github.com/exn251/Mangatan/raw/refs/heads/main/desktop-script.user.js)** from this repository.

---

## 💻 Desktop Setup Options

### Option A: Combined Server (Recommended Python Setup)
This option uses **[uv](https://docs.astral.sh/uv/getting-started/installation/)** (a fast Python package manager) to install dependencies and run the background OCR server.

1. **Download or clone this repository** to your local machine.
2. Install **`uv`** on your system.
3. Open a terminal, navigate into the `ocr-server` folder, and launch your chosen processing engine:

| Processing Engine | Host System Requirements | Launch Command |
| :--- | :--- | :--- |
| **Google Lens** | Active Internet Connection | `uv run server.py` |
| **OneOCR** | Standard CPU | `uv run server.py -e=oneocr` |
| **OneOCR (with Furigana Filter)** | Standard CPU | `uv run server.py -e=oneocrfurigana` |
| **Manga-OCR (CPU)** | Standard CPU | `uv run server.py -e=mangaocr` |
| **Manga-OCR-DirectML (Fast)** | Windows GPU (NVIDIA, AMD, Intel) | `uv run server.py -e=mangaocrdirectml` |

---

### Option B: Legacy Node.js Server - Google Lens (Alternative)
Use this if you prefer running the background Google Lens OCR process via Node.js.

1. **Download or clone this repository** to your local machine.
2. Install **[Node.js](https://nodejs.org/en/download/)** on your computer.
3. Open a terminal in the `ocr-server-legacy` directory and install the modules:
   ```bash
   npm ci
   ```
4. Start the background server:
   ```bash
   node server.js
   ```
   *(Windows users can also double-click `Runme.bat`)*

---

### Option C: Legacy Local Python Server - OneOCR (Alternative)
Use this if you prefer running local OCR (OneOCR) utilizing native Python packages.

1. **Download or clone this repository** to your local machine.
2. Ensure Python and **[OneOCR](https://github.com/AuroraWright/oneocr)** are installed.
3. Open your terminal in the `ocr-server-legacy` directory and run:
   ```bash
   pip install oneocr waitress flask aiohttp Pillow "Flask[async]"
   ```
4. Start the server:
   ```bash
   python local_server.py
   ```
   *(Or launch `runme(local-server).bat` on Windows)*
5. Ensure the **OCR Server URL** in your Tampermonkey userscript settings (gear icon) is set to `http://127.0.0.1:3000`.


---

## 📱 Mobile & Remote Setup Options

> ### ⚠️ Mobile Recommendation Disclaimer
> Setting up node, Java, and background server processes inside Termux is complex, prone to performance issues, and resource-heavy on mobile hardware. If you are looking for a high-quality mobile reading experience, utilizing dedicated native apps is strongly recommended over this manual local server workflow:
> * **For Android:** Use **[Chimahon](https://github.com/sohilsayed/Chimahon)** (a specialized Mihon/Komikku immersion fork with native Yomitan dictionary lookup, Mokuro support, novel EPUB reader, and instant card mining).
> * **For Android & iOS:** Use **[Manatan](https://github.com/KolbyML/Manatan)** (the streamlined, cross-platform application for reading and language immersion on all platforms).
> 
> *If you still prefer to host the background server and browser overlay setup manually on mobile, you can use the instructions below.*

### Option 1: Local Android Setup (Via Termux)
Runs both Suwayomi-Server and the background OCR server directly on your mobile device.

> **Recommended Browsers:** Firefox or Edge Canary. Install Tampermonkey in the browser and add the userscript.

1. Install **Termux** via **[F-Droid](https://f-droid.org/en/packages/com.termux/)** or **[GitHub Releases](https://github.com/termux/termux-app/releases)**.
2. **Install Suwayomi-Server:** Open Termux and run this installer command:
   ```sh
   pkg update -y && pkg install -y openjdk-21 wget jq libandroid-posix-semaphore && mkdir -p ~/suwayomi/bin && LATEST_JAR_URL=$(curl -s https://api.github.com/repos/Suwayomi/Suwayomi-Server/releases/latest | jq -r '.assets[] | select(.name | endswith(".jar")) | .browser_download_url') && wget -O ~/suwayomi/SuwayomiServer.jar "$LATEST_JAR_URL" && echo -e '#!/data/data/com.termux/files/usr/bin/bash\njava -jar ~/suwayomi/SuwayomiServer.jar' > ~/suwayomi/bin/suwayomi && chmod +x ~/suwayomi/bin/suwayomi && echo 'export PATH="$HOME/suwayomi/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
   ```
3. **Install the OCR Server:** Open a second Termux session/tab and run this command:
   ```sh
   rm -rf ~/Mangatan && pkg install -y git nodejs && git clone https://github.com/kaihouguide/Mangatan && cd Mangatan/ocr-server-legacy && npm install express chrome-lens-ocr multer node-fetch --force && mkdir -p ~/bin && echo -e '#!/data/data/com.termux/files/usr/bin/sh\ncd ~/Mangatan/ocr-server-legacy && node server.js' > ~/bin/mangatan && chmod +x ~/bin/mangatan && echo 'export PATH=$HOME/bin:$PATH' >> ~/.bashrc && source ~/.bashrc
   ```
4. **Configure Android WebAssembly Compatibility:** Run these dependencies individually in your second Termux session:
   ```sh
   npm install --cpu=wasm32 sharp --force
   ```
   ```sh
   npm install @img/sharp-wasm32 --force
   ```
   ```sh
   rm -rf node_modules package-lock.json
   ```
   ```sh
   npm install --force
   ```

#### To Read on Android:
* Type `suwayomi` in Termux Session 1.
* Type `mangatan` in Termux Session 2.
* Open your mobile browser and navigate to `http://127.0.0.1:4567`.
* **Controls:** Long-press an image to hide or show the OCR overlay. Tap boxes to focus, and make use of the slide-out FAB menu to toggle edit/merge modes.

---

### Option 2: Remote Setup (Host Computer to Mobile)
This allows hosting both Suwayomi-Server and the OCR Server on a powerful desktop while reading on a tablet or phone over your home Wi-Fi network.

1. **Host Configuration:**
   * Locate your Suwayomi `server.conf` directory:
     * **Windows:** `%LOCALAPPDATA%/Tachidesk`
     * **Linux:** `~/.local/share/Tachidesk`
     * **macOS:** `~/Library/Application Support/Tachidesk`
   * Open `server.conf` and change `server.ip` to match your desktop's local network IP address (e.g., `192.168.1.50`).
   * Launch your desktop OCR server with the IP command argument:
     ```bash
     node server.js --ip <your_desktop_ip>
     ```
2. **Mobile Configuration:**
   * Open the Tampermonkey dashboard on your mobile device.
   * Edit the Mangatan script, replacing instances of `127.0.0.1` or `localhost` with your desktop's local IP address.

#### 💡 Process Automation (NSSM on Windows)
You can use **NSSM** (Non-Sucking Service Manager) to run both utilities in the background as services. Configure them using these batch file templates:

* **Suwayomi Startup (`suwayomi_start.bat`):**
  ```bat
  @echo off
  cd /d "<path-to-suwayomi-server-folder>"
  "<path-to-suwayomi-server-folder>\jre\bin\java.exe" -jar "<path-to-suwayomi-server-folder>\bin\Suwayomi-Server.jar"
  ```
* **OCR Startup (`ocr_start.bat`):**
  ```bat
  @echo off
  cd /d "<path-to-suwayomi-server-folder>\ocr-server-legacy"
  node server.js --cache-path "<your-cache-path>" --ip <your-ip-address> --port <your-port>
  ```

---

## 💾 Caching & Cache Management
* The OCR server automatically creates and writes to `ocr-cache.json` in its root folder to store text changes.
