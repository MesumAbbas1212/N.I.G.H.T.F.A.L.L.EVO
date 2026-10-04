# ⚡ NIGHTFALL AI Evo — Autonomous Desktop Operating Intelligence

<div align="center">

<img src="assets/NIGHTFALL_evo_logo.png" alt="NIGHTFALL AI Evo Logo" width="220" />

### *The Self-Evolving, Multimodal Personal AI Desktop Environment*

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue?logo=python&logoColor=white)](https://python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6%20%2B%20WebEngine-darkgreen?logo=qt&logoColor=white)](https://riverbankcomputing.com/software/pyqt/)
[![Gemini 2.5](https://img.shields.io/badge/Model-Gemini%202.5%20Flash%20Native%20Audio-orange?logo=google&logoColor=white)](https://ai.google.dev/)
[![MCP](https://img.shields.io/badge/Protocol-Model%20Context%20Protocol-purple)](https://modelcontextprotocol.io/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-0078D6?logo=windows&logoColor=white)](https://microsoft.com/windows)

</div>

---

## 🌌 Overview

**NIGHTFALL AI Evo** is a next-generation desktop intelligence built to function like an authentic JARVIS workstation. Combining real-time bi-directional native voice audio, multimodal computer vision, autonomous code self-evolution, and local system orchestrations, NIGHTFALL Evo transforms your Windows PC into a self-evolving command center.

---

## ⚡ What's New in NIGHTFALL AI Evo

### 1. 🔌 Holographic Hardware Assembler & Circuit HUD
- **Screen & Voice Part Recognition**: NIGHTFALL scans your screen via computer vision or parses voice commands (`"NIGHTFALL, how to connect DHT11 to Arduino Pro Mini"`) to recognize microcontrollers (Arduino Uno, Pro Mini, ESP32), sensors (DHT11, HC-SR04 ultrasonic, servos), and passive components.
- **In-App Interactive Pop-up**: Opens a compact, dark glassmorphic popup overlay directly inside NIGHTFALL showing:
  - Component cards with pinout labels (`VCC`, `DATA`, `GND`, `2`, `p8`, `p9`).
  - **Animated glowing neon SVG wires** with real-time flowing white electron pulse dots.
  - Numbered pin bubbles (`①`, `②`, `③`, `④`, `⑤`).
  - Operating voltage safety callouts and ready-to-flash Arduino C++ firmware.

### 2. 🧬 Project Ultron — Self-Evolving Autonomous Skill Crucible
- **On-the-Fly Code Synthesis**: When asked to execute a task outside its built-in toolkit, NIGHTFALL identifies the capability gap, writes a brand-new Python tool directly into `features/`, tests it inside an isolated sandbox ("The Crucible"), and auto-registers it dynamically without restarting the application.
- **Persistent Vault**: All forged skills are saved in your `features/` directory and hot-reloaded automatically.

### 3. 🛡️ Proactive Auto-Heal Engine
- **Self-Repairing Codebase**: Background sentry catches runtime exceptions, analyzes tracebacks using LLM root-cause reasoning, applies dynamic patches to the faulty code, and presents an interactive HUD repair telemetry card without crashing the assistant.

### 4. 🌊 Real-Time FFT Audio Waveform & Dynamic Glow Core
- **Live Physical Frequency Spectrum**: 9-bar reactive audio visualizer embedded directly into the Command Bar.
- **Dynamic State Glow States**:
  - 🟡 **Gold**: Standby / Listening
  - 🔵 **Cyan / Blue**: Capturing Voice (Live Energy Wave)
  - 🟣 **Purple**: NIGHTFALL Reasoning / Thinking
  - 🟢 **Green**: Executing Tool / System Action
  - 🔴 **Red**: Muted

### 5. 🧩 Visual Chain-of-Thought Execution Tracker
- **Agentic Step-by-Step HUD Cards**: Collapsible execution traces embedded directly into chat bubbles.
- Features sub-second execution latency chips (`[180ms]`, `[120ms]`), status badges (`COMPLETED`), and expandable `[Inspect]` drawers for parameters and raw JSON payloads.

### 6. 🌐 Interactive 3D HoloGlobe & Geospatial Radar
- **3D Earth & 2D Street Mapping**: WebGL spherical Earth globe with live great-circle flight routes, real-time aircraft radar tracking, turn-by-turn highway driving routes, and nearby POI scans (hospitals, fuel, ATMs).

### 7. 🎵 Official Spotify MCP Server Integration
- Built on the open **Model Context Protocol (MCP)** for robust, reliable music playback, album/artist searches, queue control, volume adjustment, and active device handover between PC and mobile.

### 8. ⚙️ Centralized API Setup Hub
- Complete Settings UI featuring dedicated cards for every connected service:
  - Google Gemini API (Live Voice & Vision)
  - Spotify MCP (Client ID & Client Secret)
  - OpenRouter / Anthropic
  - Weather & Radar APIs
  - Instagram & Smart Home
  - Instant live test-connection buttons.

---

## 🛠️ Core Capabilities

### 🎙️ Multimodal Native Audio & Vision
- Sub-500ms low-latency conversation via Gemini 2.5 Flash Native Audio.
- Live webcam and desktop screen vision for real-time document analysis, code debugging, and hardware component recognition.

### 🖥️ Deep Windows Desktop Orchestration
- **Application & Process Manager**: Launch, control, inspect, and freeze memory-heavy background processes.
- **System Settings**: Toggle Wi-Fi, adjust volume, screen brightness, dark/light themes, and monitor CPU/RAM/Battery metrics.
- **Smart Window Control**: Minimize, maximize, tile, or arrange windows across multi-monitor setups.
- **Clipboard Sentry**: Instant analysis, translation, formatting, or debugging of copied text/code.

### 📄 Autonomous Office & Document Architect
- **Word (`.docx`)**: Generate formatted professional documents, reports, and resumes.
- **Excel (`.xlsx`)**: Create complex multi-sheet workbooks with formulas, charts, and financial models.
- **PowerPoint (`.pptx`)**: Build branded presentations with slide layouts, typography, and speaker notes.
- **PDF Suite**: Convert, merge, extract, and assemble PDF deliverables.

### 📱 NIGHTFALL Connect (Android Companion)
- **AI Phone Call Proxy**: NIGHTFALL screens incoming phone calls, talks to the caller, takes meeting notes, and delivers immediate desktop transcripts and summaries.
- **Ecosystem Sync**: Device geolocation, SMS notifications, and battery status.

### 🏡 Smart Home Hub
- Control smart lights, smart plugs, AC units, fans, and televisions with direct Tuya and Home Assistant integrations.

### 🧠 Long-Term Memory & Identity
- Adaptive memory engine stores personal user preferences, ongoing projects, and custom instructions across sessions.

### 🔑 Any Provider, Any Model
- **Custom API keys**: add keys for any provider from *Settings → AI Providers → Custom AI Providers* — famous ones
  (Groq, Together, Mistral, DeepSeek, Cerebras, xAI, Fireworks, Perplexity, Cohere, AI21, OpenRouter, Anthropic,
  Google, …) or brand-new services. Each entry stores its own key, base URL, default model and capabilities, and any
  OpenAI-, Anthropic- or Gemini-compatible endpoint works — including self-hosted Ollama, LM Studio or vLLM servers.
- A custom provider can also be selected as the **default AI provider**, and custom providers are used automatically
  for Quick Actions, vision fallbacks and document generation.

### 🧭 Jeff Model Routing
- [jeff](https://github.com/logan-markewich/jeff) is a self-hosted drop-in replacement for TypeSafe's *jev System One
  API*: given a request it answers small classification questions (`choice`, `score`, `noul`) about it.
- NIGHTFALL Evo asks Jeff *which capability a request needs* and *how complex it is*, then routes the request to the
  best provider/model you have configured (coding → a coder, long documents → a large-context model, screenshots → a
  vision model, quick chat → the fastest/cheapest one). The decision is logged in the chat
  (`Jeff → Groq [llama-3.3-70b] (capability=coding, complexity=low, router=jeff)`).
- Run your own server with `JEFF_API_KEYS=devkey uv run jeff`, then enable it in
  *Settings → Jeff Model Routing* (server URL, key, model). Without a server, a built-in heuristic router is used, so
  routing never breaks.

### 🪶 Resource use
- Heavy stacks stay out of the boot path: OpenCV, mss, PortAudio and numpy are imported only when screen or
  camera capture actually runs, so startup no longer pays for features you may never use.
- Settings and key files are cached against their modification time instead of being re-read several times per
  request, and the scan overlay's 60 fps repaint loop stops while the overlay is hidden.
- Every provider that is configured is visible to the assistant: *Settings → Custom AI Providers* and the
  assistant's own provider tool read and write the same store, so "do you see <provider>?" answers match the screen.

### 🧯 When a free key runs out
- Free tiers are small — Google's free Gemini plan allows only ~20 requests per day *per model*, which a couple of
  documents or Quick Actions can use up. When that happens NIGHTFALL Evo says so in one short sentence instead of
  pasting the provider's error, stops asking the exhausted provider, and finishes the job with another provider if
  you have one.
- Adding a second key (Groq, OpenRouter and Cerebras all have free tiers) in *Settings → Custom AI Providers* is the
  recommended fix; documents then fall back to it automatically. A local Ollama/LM Studio model is used too, if one
  is running, so a document can still be written with no cloud key at all.

---

## 🚀 Quick Start

### Prerequisites
- **Windows 10 / 11** (64-bit)
- **Python 3.11** or **Python 3.12**
- **Git**
- Working Microphone & Speakers (Webcam optional for vision)
- **Google Gemini API Key** (Get from [Google AI Studio](https://aistudio.google.com/))

### Installation

1. **Clone the repository:**
   ```powershell
   git clone https://github.com/titechprabhasolutions/NIGHTFALL-Ai-Evo.git
   cd NIGHTFALL-Ai-Evo
   ```

2. **Run the Automated Setup:**
   ```powershell
   python setup.py
   ```
   *Or launch using the included batch file:*
   ```cmd
   start_NIGHTFALL.bat
   ```

3. **Configure API Keys:**
   - Launch NIGHTFALL AI Evo.
   - Click the **Settings** icon on the top navigation bar.
   - Enter your **Gemini API Key** and any optional credentials (Spotify MCP, Weather, etc.) into the respective cards.
   - Click **Save & Connect**.

---

## 🎮 Voice & Command Cheat Sheet

| Intent | Sample Voice / Text Command |
| :--- | :--- |
| **Hardware Circuit** | *"NIGHTFALL, how to connect DHT11 to Arduino Pro Mini"* |
| **Circuit Vision** | *"See the Arduino parts on my screen and tell me how to assemble them"* |
| **Self-Evolution** | *"NIGHTFALL, learn a new skill to track International Space Station coordinates"* |
| **Flight Radar** | *"Show flight route from Mumbai to London"* |
| **Nearby Amenities** | *"Find nearby hospitals on the map"* |
| **Music Playback** | *"Play Starboy on Spotify"* |
| **Phone Screening** | *"Screen this incoming call for me"* |
| **Office Documents** | *"Create a project roadmap spreadsheet in Excel"* |
| **System Health** | *"Check system resources and kill heavy apps"* |

---

## 🏗️ Architecture

```
NIGHTFALL AI Evo/
├── main.py                     # Main application entry point & live event loop
├── ui.py                       # PyQt6 GUI: Command Bar, Waveform FFT, HUD Wings, Chat
├── actions/                    # Built-in action tools & executors
│   ├── circuit_assembler.py    # Hardware vision & circuit solver engine
│   ├── spotify_controller.py   # Spotify MCP client & playback manager
│   ├── geospatial_globe.py     # Great-circle routes, radar & POI fetcher
│   ├── auto_heal_engine.py     # Proactive exception interceptor & patcher
│   ├── call_assistant.py       # Android phone call proxy & transcript analyzer
│   └── office_builder.py       # Excel, Word, PPTX & PDF generators
├── core/                       # Core system architecture
│   ├── circuit_hud.py          # Compact in-app holographic circuit popup overlay
│   ├── globe_window.py         # 3D WebGL Earth globe & map controller
│   ├── skill_forge.py          # Project Ultron: LLM skill synthesizer
│   ├── skill_crucible.py       # Isolated test execution sandbox
│   ├── dynamic_registry.py     # Runtime tool hot-reloader & dispatcher
│   └── identity.py             # System prompt & behavioral core
├── features/                   # Self-evolved & custom Python tools
│   ├── circuit_schematic.py    # Modular circuit schematic feature
│   └── spotify_mcp.py          # Modular Spotify feature
├── smart_home/                 # Smart device provider & discovery services
└── NIGHTFALL-connect-android/     # Companion Android mobile application
```

---

## 🔒 Security & Privacy

- All long-term memories, credentials, and configuration files are stored locally in `%LOCALAPPDATA%\NIGHTFALLAI\`.
- External tools run through permission sentries and the isolated Crucible sandbox.
- Audio and video frames are only streamed during active conversation sessions.

---

## 📄 License

**Personal & Private Local Use Only — No Distribution.**

This project is licensed under the **NIGHTFALL AI Evo Source-Available Personal Use License**.

- ✅ **Allowed:** You may download, clone, inspect, build, and run the software locally strictly on your own personal device for private, personal, educational, and research use.
- 🚫 **Strictly Prohibited (No Distribution):** You may **NOT** distribute, redistribute, re-upload, mirror, share, transmit, sublicense, or publish this software, repository, binaries, or derivative works anywhere (including other Git hosts, public repositories, or cloud platforms). The only official distribution source is this repository.
- 🚫 **Strictly Prohibited (No Commercial Use):** You may **NOT** sell, rent, monetize, bundle, commercialize, or host this software as a paid SaaS/cloud service.

For full terms and legal conditions, see the [LICENSE](LICENSE) file. For commercial licensing inquiries, reach out via [Discord](https://discord.gg/gEYmJKKtq3).

<div align="center">
<b>NIGHTFALL AI Evo</b> • Built with intelligence, precision, and autonomy.
</div>
