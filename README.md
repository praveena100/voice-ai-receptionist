# Production Voice AI Receptionist

A production-grade, real-time Voice AI Receptionist designed to handle live telephone conversations with **under-500ms voice response latency**, instant **barge-in (interruption handling)**, and direct appointment scheduling into **Google Calendar**.

---

## ⚡ Architectural Overview & Tech Stack

```
[ Caller Audio ] 
       │ (WebRTC Bi-directional Audio Stream)
       ▼
┌────────────────────────────────────────────────────────┐
│               LiveKit Agents SDK (Python)              │
│  - WebRTC Transport & Room Management                  │
│  - Silero VAD (Voice Activity Detection & Barge-in)    │
│  - Conversational Turn Detector & Pipeline Controller  │
└──────┬──────────────────────┬───────────────────┬──────┘
       │                      │                   ▲
       ▼                      ▼                   │
┌──────────────┐       ┌──────────────┐    ┌──────────────┐
│   Deepgram   │       │   Groq LPU   │    │   Cartesia   │
│    Nova-2    │──────▶│  gpt-oss-120b│───▶│    Sonic     │
│ (Streaming   │ Text  │  (or 70B)    │Text│ (Streaming   │
│     STT)     │       │ (OpenAI API) │    │     TTS)     │
└──────────────┘       └──────┬───────┘    └──────────────┘
                              │
                      [Function Calling]
                              │
                              ▼
                   ┌──────────────────────┐
                   │  Google Calendar API │
                   │  (Service Account)   │
                   │  - Check Free/Busy   │
                   │  - Book Appointments │
                   └──────────────────────┘
```

| Component | Technology | Role & Performance |
| :--- | :--- | :--- |
| **Orchestration & Transport** | **LiveKit Agents SDK** | Ultra-low latency WebRTC, bi-directional audio streaming, room lifecycle |
| **VAD & Interruption** | **Silero VAD** | Prewarmed on process start; enables instant interruption (barge-in) when user speaks |
| **Speech-to-Text (STT)** | **Deepgram Nova-2** | Streaming WebSocket audio transcription with punctuation and smart formatting (~150ms) |
| **AI Brain (LLM)** | **Groq API** (`openai/gpt-oss-20b` / `llama-3.3-70b-versatile`) | Ultra-fast LPU inference (500+ tokens/sec) via OpenAI-compatible endpoint (~120ms TTFT) |
| **Text-to-Speech (TTS)** | **Cartesia Sonic** | Low-latency voice streaming synthesis with human-like prosody (~100ms) |
| **Business Logic & Tools** | **Google Calendar API** | Automated slot availability verification and appointment scheduling via Service Account |

---

## 📁 Repository Structure

```
voice-receptionist/
├── .env.example              # Environment variables template
├── requirements.txt          # Python dependencies
├── credentials.json.example  # Example Google Service Account credentials structure
├── calendar_tool.py          # Google Calendar Manager & diagnostic tool
├── agent.py                  # Main LiveKit VoicePipelineAgent worker script
└── README.md                 # Setup, configuration, and testing guide
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites

- **Python 3.10+** (Tested on Python 3.12)
- Accounts and API Keys for:
  - [LiveKit Cloud](https://cloud.livekit.io)
  - [Groq Console](https://console.groq.com/keys)
  - [Deepgram Console](https://console.deepgram.com)
  - [Cartesia Console](https://play.cartesia.ai)
  - [Google Cloud Console](https://console.cloud.google.com) (Google Calendar API enabled)

---

### 2. Installation

1. **Clone the repository and enter the directory**:
   ```bash
   cd voice-receptionist
   ```

2. **Create and activate a virtual environment**:
   ```bash
   # Windows:
   python -m venv venv
   .\venv\Scripts\activate

   # Linux / macOS:
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Pre-download model assets (Silero VAD)**:
   ```bash
   python agent.py download-files
   ```

---

### 3. Environment Configuration

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Edit `.env` with your actual API credentials:

```dotenv
# LiveKit Cloud
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# Groq LPU
GROQ_API_KEY=gsk_your_groq_api_key

# Deepgram STT
DEEPGRAM_API_KEY=your_deepgram_api_key

# Cartesia TTS
CARTESIA_API_KEY=your_cartesia_api_key
CARTESIA_VOICE_ID=f786b574-daa5-4673-aa0c-cbe3e8534c02

# Google Calendar
GOOGLE_SERVICE_ACCOUNT_FILE=credentials.json
GOOGLE_CALENDAR_ID=primary
TIMEZONE=America/New_York
```

---

### 4. Setting Up Google Calendar Service Account

To enable the AI Receptionist to write to your Google Calendar:

1. **Create / Open a GCP Project**:
   - Go to [Google Cloud Console](https://console.cloud.google.com/).
2. **Enable Google Calendar API**:
   - Navigate to **APIs & Services** > **Library**.
   - Search for **Google Calendar API** and click **Enable**.
3. **Create a Service Account**:
   - Navigate to **APIs & Services** > **Credentials**.
   - Click **Create Credentials** > **Service Account**.
   - Name it `voice-receptionist` and click **Done**.
4. **Generate JSON Key**:
   - Click on the created service account.
   - Go to the **Keys** tab > **Add Key** > **Create New Key** > Select **JSON**.
   - Save the downloaded file into this project root as `credentials.json`.
5. **Share Your Calendar with the Service Account**:
   - Open [Google Calendar](https://calendar.google.com).
   - Find the calendar you want the bot to manage under "My calendars".
   - Click the 3 dots next to it > **Settings and sharing**.
   - Under **Share with specific people or groups**, click **Add people**.
   - Enter your service account's `client_email` (e.g. `voice-receptionist@your-gcp-project.iam.gserviceaccount.com`).
   - Set Permission to: **"Make changes to events"**.
   - If using your personal calendar, set `GOOGLE_CALENDAR_ID=your_email@gmail.com` in `.env` (or keep `primary` if using the service account's own calendar).

#### Verify Google Calendar Integration:
Run the diagnostic script to test your calendar connection:
```bash
python calendar_tool.py
```
If configured correctly, it will print:
```
[+] Connection successful!
[+] Found upcoming events on calendar '...'
```

---

## 🎙️ Running the AI Receptionist

### Development Mode (with Live Reload)
```bash
python agent.py dev
```

The agent worker will register with your LiveKit server and await incoming room connections.

---

## 🧪 Testing the Voice Agent

### Option 1: LiveKit Agents Playground
1. Visit the [LiveKit Agents Playground](https://agents-playground.livekit.io/).
2. Connect using your LiveKit Cloud project URL and generate a token in your LiveKit Cloud dashboard.
3. Allow microphone access and begin speaking with Sarah!

### Sample Conversation Walkthrough

```text
Receptionist: "Hello! Thank you for calling Apex Wellness. My name is Sarah. How can I assist you with scheduling today?"
Caller:       "Hi Sarah, I'd like to book a dental checkup for tomorrow afternoon around 2 PM."
Receptionist: [Calls check_availability("2026-10-26T14:00:00", duration=30)]
Receptionist: "I have Monday, October 26th at 2:00 PM open. May I please have your full name and phone number to book that for you?"
Caller:       "My name is John Doe, and my phone number is 555-0199."
Receptionist: "Thank you, John. Just to confirm, shall I book your dental checkup for Monday, October 26th at 2:00 PM?"
Caller:       "Yes, please!"
Receptionist: [Calls book_appointment(summary="Dental Checkup - John Doe", start_time="2026-10-26T14:00:00", ...)]
Receptionist: "I have booked your dental checkup for Monday, October 26th from 2:00 PM to 2:30 PM. Is there anything else I can help you with today?"
```

### Testing Barge-in (Interruption Handling)
- While the receptionist is in the middle of speaking, speak naturally into your microphone (e.g., *"Wait, actually could we do 3 PM instead?"*).
- Silero VAD detects speech in real-time, instantly cuts off the TTS audio playback, cancels generation, and answers your new statement.

---

## 📞 Connecting to Real Phone Numbers (Telephony / SIP Trunking)

To allow callers to dial a real telephone number (e.g. from their mobile phone) and speak with Sarah:

1. **Get a Phone Number on Twilio or Telnyx**:
   - Buy a local or toll-free number (e.g. `+1 (555) 123-4567`).
2. **Create a LiveKit SIP Trunk**:
   - In [LiveKit Cloud Console](https://cloud.livekit.io/), navigate to **SIP** > **Inbound Trunks**.
   - Create a new Inbound Trunk and link it to your LiveKit project.
   - LiveKit provides a SIP URI (e.g., `sip:your-project.sip.livekit.cloud`).
3. **Configure Twilio SIP Trunking**:
   - In Twilio Console > **Elastic SIP Trunking** > **Origination**, add the LiveKit SIP URI.
   - Set up an Inbound Dispatch rule routing incoming calls directly into your agent's room.
4. **Result**:
   - When a customer dials your phone number, Twilio forwards audio over SIP to LiveKit, which immediately spins up an instance of `agent.py`!

---

## 🐳 Docker & Cloud Deployment

### Run 24/7 with Docker Compose:
```bash
docker compose up -d --build
```
To view live logs:
```bash
docker compose logs -f
```

### Production Best Practices:
1. **Prewarming VAD**:
   - `prewarm(proc: JobProcess)` loads the Silero VAD neural network into the parent worker memory before forks occur, reducing incoming call connection latency to <100ms.
2. **Sub-300ms Turn Detection**:
   - `min_endpointing_delay=0.3` balances conversational snappy turnaround without cutting callers off mid-breath.
3. **Past-Time Intelligence**:
   - Sarah automatically rolls past hours forward to tomorrow if a caller requests a slot that has already passed today.
4. **Deploying on a VPS (AWS EC2 / DigitalOcean)**:
   - Run the docker container or use `systemd` to keep `python agent.py start` running in the background indefinitely.
