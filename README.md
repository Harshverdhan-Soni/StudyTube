# StudyTube

Type a topic. You get the **single most relevant YouTube video** (playable in the app) and
**study notes from its transcript** that you can read instead of watching. The notes include a TL;DR,
key concepts, detailed notes, revision questions, and clickable timestamps that jump the video.

## Install (one time)
You need **Python 3.9+** (Windows: install from python.org and tick *"Add python.exe to PATH"*).

| OS | Do this |
|---|---|
| **Windows** | Unzip the folder somewhere permanent (e.g. `Documents\StudyTube`), then double-click **`INSTALL-Windows.bat`** |
| **Linux** | `bash install-linux.sh` |
| **macOS** | Right-click **`install-mac.command`**, then choose Open |

The installer creates a **StudyTube icon on your Desktop** (and in the Start Menu on Windows).
Double-click it and the app opens in its own window, with no terminal or browser tab.
The first time, it asks for your keys. You can change them later with the ⚙ button.

> Keep the folder where it is after installing. The shortcut points to it.

## Keys (all free, no credit card)
| What | Service | Get it |
|---|---|---|
| Search | YouTube Data API v3 | Google Cloud Console |
| Notes (default) | Gemini API free tier | Same Google key (see below) or aistudio.google.com/apikey |
| Transcribing videos with no captions (optional) | Groq Whisper free tier | console.groq.com/keys |

**One Google key for both YouTube and Gemini.** Run this in Google Cloud Shell and copy the `keyString`:
```bash
gcloud services enable youtube.googleapis.com generativelanguage.googleapis.com
gcloud services api-keys create --display-name="StudyTube" \
  --api-target=service=youtube.googleapis.com \
  --api-target=service=generativelanguage.googleapis.com
```
Paste it as the YouTube key and leave the Gemini field blank. Do **not** enable billing on that
project, so it stays on the free tier.

**How a transcript is obtained (cheapest first):**
1. YouTube's own captions: free, no key needed.
2. If there are none and a Groq key is set, the audio is transcribed with Whisper on Groq's free tier (videos up to 1 hour).
3. If that still fails and Gemini is selected, Gemini watches the video itself and writes notes. The Transcript tab is then empty.

**Other note writers:** Groq (free, but only suited to shorter videos because of its small per-minute token limit),
Ollama (local and offline: `ollama pull llama3.1`), or Claude (paid).

**About the free tiers:** they have per-minute and per-day caps. If you hit one, the app tells you to wait a minute.
Each search uses 2 Gemini calls, one to rank the videos and one to write the notes. Repeat searches are served from `cache/`.

## How it picks the video
1. Takes YouTube's top 10 results by relevance (embeddable only).
2. Drops Shorts and clips (< 3 min) and very long streams (> 3 h).
3. The AI ranks the remaining candidates *for learning*: credible educator, exact topic, no clickbait.
4. The first candidate that has a transcript wins. The others appear under "Other good options".

## Files
- `launcher.py`: what the desktop icon runs. It starts the local server and opens the app window.
  If the native window component can't load, it opens an Edge or Chrome app-style window instead.
- `app.py`: search, ranking, transcript and summary logic. `templates/index.html` is the UI.
- `.env`: your saved keys (written by the Settings screen). `cache/`: saved results. Delete it to refresh.
- `studytube.log`: error log, used when the app is started from the icon.

Developer mode: `python app.py`, then open http://127.0.0.1:5000

## Notes
- Transcripts come from `youtube-transcript-api`. It works from home or office internet, but YouTube often blocks
  cloud-server IPs, so this is designed to run on your own computer.
- English is the preferred transcript language, with Hindi as a fallback. Notes can be in English, Hindi or Hinglish.
