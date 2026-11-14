"""StudyTube: type a topic, get the single most relevant YouTube video
plus study notes from its transcript.

All default services are free:
  * search      - YouTube Data API v3 (free key)
  * transcript  - YouTube captions (no key) -> Groq Whisper if no captions (free key)
  * notes       - Gemini API free tier (default) | Groq | Ollama (local) | Anthropic

Run:  python app.py   then open http://127.0.0.1:5000   (or use the desktop launcher)
"""
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from youtube_transcript_api import YouTubeTranscriptApi

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)

CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
ytt = YouTubeTranscriptApi()

PROVIDERS = ("gemini", "groq", "ollama", "anthropic")


def cfg(name, default=""):
    """Read settings live, so changes from the Settings screen apply at once."""
    return os.getenv(name, default).strip()


def chosen_provider():
    p = cfg("LLM_PROVIDER", "gemini").lower()
    return p if p in PROVIDERS else "gemini"


def provider_has_key(p):
    return {"gemini": bool(gemini_key()), "groq": bool(cfg("GROQ_API_KEY")),
            "ollama": True, "anthropic": bool(cfg("ANTHROPIC_API_KEY"))}[p]


def provider():
    """The chosen provider, or free Gemini if the chosen one has no key yet
    (e.g. an old .env still saying 'anthropic')."""
    p = chosen_provider()
    return p if provider_has_key(p) else "gemini"


def gemini_key():
    # One Google key can serve both YouTube and Gemini if it allows both APIs.
    return cfg("GEMINI_API_KEY") or cfg("YOUTUBE_API_KEY")


# Rough per-provider input budgets (characters of transcript sent in one call).
# Gemini Flash has a 1M-token window; Groq's free tier has small tokens/minute caps.
CHAR_BUDGET = {"gemini": 400_000, "anthropic": 400_000, "ollama": 40_000, "groq": 24_000}

MIN_SECONDS = int(cfg("MIN_VIDEO_SECONDS", "180"))       # skip Shorts / clips
MAX_SECONDS = int(cfg("MAX_VIDEO_SECONDS", str(3 * 3600)))
TRANSCRIPT_LANGS = cfg("TRANSCRIPT_LANGS", "en,en-IN,en-US,en-GB,hi").split(",")


class RateLimited(Exception):
    pass


# ---------------------------------------------------------------- helpers
def cache_get(key):
    f = CACHE_DIR / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def cache_put(key, value):
    (CACHE_DIR / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def h(*parts):
    return hashlib.sha1("|".join(parts).lower().strip().encode()).hexdigest()[:16]


def iso_duration_to_seconds(d):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    days, hrs, mins, secs = (int(x) if x else 0 for x in m.groups())
    return days * 86400 + hrs * 3600 + mins * 60 + secs


def fmt_ts(sec):
    sec = int(sec)
    hh, rem = divmod(sec, 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"


def post_with_retry(url, tries=3, **kw):
    """POST that waits and retries on free-tier rate limits (429) and overloads (503)."""
    for attempt in range(tries):
        r = requests.post(url, timeout=kw.pop("timeout", 300), **kw)
        if r.status_code not in (429, 500, 503) or attempt == tries - 1:
            break
        wait = r.headers.get("retry-after")
        time.sleep(min(float(wait), 40) if wait and wait.replace(".", "").isdigit() else 8 * (attempt + 1))
    if r.status_code == 429:
        raise RateLimited("Free-tier limit reached for now. Wait a minute and try again.")
    if not r.ok:
        raise RuntimeError(f"{url.split('/')[2]} returned {r.status_code}: {r.text[:300]}")
    return r


# ---------------------------------------------------------------- LLM providers
class ModelBusy(Exception):
    """The model is overloaded / unavailable / unknown: worth trying another one."""


def open_stream(url, tries=2, **kw):
    """POST with stream=True. One quick retry on overload, then let the caller switch model."""
    timeout = kw.pop("timeout", (15, 300))
    for attempt in range(tries):
        r = requests.post(url, stream=True, timeout=timeout, **kw)
        if r.status_code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
            break
        r.close()
        wait = r.headers.get("retry-after")
        time.sleep(min(float(wait), 6) if wait and wait.replace(".", "").isdigit() else 3)
    if r.ok:
        return r
    host = url.split("/")[2]
    try:
        detail = r.json()["error"]["message"]
    except Exception:
        detail = r.text[:200]
    if r.status_code in (404, 429, 500, 502, 503, 504):
        raise ModelBusy(f"{host} {r.status_code}: {detail}")
    raise RuntimeError(f"{host} returned {r.status_code}: {detail}")


def sse_json(resp):
    """Yield the JSON payloads of a server-sent-events response."""
    for line in resp.iter_lines(decode_unicode=True):
        if line and line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                yield json.loads(data)


def gemini_models(fast):
    first = cfg("GEMINI_FAST_MODEL", "gemini-flash-lite-latest") if fast else cfg("GEMINI_MODEL", "gemini-flash-latest")
    rest = cfg("GEMINI_FALLBACK_MODELS", "gemini-flash-lite-latest,gemini-flash-latest,gemini-2.5-flash").split(",")
    seen, out = set(), []
    for m in [first] + [x.strip() for x in rest]:
        if m and m not in seen:
            seen.add(m); out.append(m)
    return out[:2] if fast else out   # ranking is optional: don't spend long on it


def llm_stream(system, prompt, max_tokens=4000, youtube_url=None, fast=False, use=None):
    """Yield the answer in small text chunks as it is written.
    May also yield {"switch": message} when it has to fall back to another model."""
    p = use or provider()

    if p == "gemini":
        parts = [{"text": prompt}]
        if youtube_url:  # Gemini can watch a public YouTube video directly
            parts.insert(0, {"file_data": {"file_uri": youtube_url}})
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}}
        models, last = gemini_models(fast), None
        for i, model in enumerate(models):
            if i:
                yield {"switch": f"{models[i-1]} is busy, trying {model}…"}
            try:
                r = open_stream(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse",
                    headers={"x-goog-api-key": gemini_key(), "Content-Type": "application/json"},
                    json=body, timeout=(15, 60 if fast else 300))
            except ModelBusy as e:
                last = e
                app.logger.info("gemini model %s unavailable: %s", model, e)
                continue
            for ev in sse_json(r):
                for c in ev.get("candidates") or []:
                    for pt in c.get("content", {}).get("parts", []):
                        if pt.get("text") and not pt.get("thought"):
                            yield pt["text"]
            return
        raise ModelBusy(f"All free Gemini models are busy right now ({last}).")

    if p == "groq":
        r = open_stream(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {cfg('GROQ_API_KEY')}"},
            json={"model": cfg("GROQ_FAST_MODEL", "llama-3.1-8b-instant") if fast else cfg("GROQ_MODEL", "llama-3.3-70b-versatile"),
                  "max_tokens": min(max_tokens, 4000), "temperature": 0.3, "stream": True,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": prompt}]},
        )
        for ev in sse_json(r):
            delta = (ev.get("choices") or [{}])[0].get("delta", {}).get("content")
            if delta:
                yield delta
        return

    if p == "ollama":
        r = requests.post(
            f"{cfg('OLLAMA_URL', 'http://localhost:11434')}/api/chat", stream=True, timeout=(10, 900),
            json={"model": cfg("OLLAMA_MODEL", "llama3.1"), "stream": True,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        for line in r.iter_lines():
            if line:
                yield json.loads(line).get("message", {}).get("content", "")
        return

    import anthropic  # paid option
    with anthropic.Anthropic(api_key=cfg("ANTHROPIC_API_KEY")).messages.stream(
            model=cfg("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=max_tokens,
            system=system, messages=[{"role": "user", "content": prompt}]) as s:
        yield from s.text_stream


def llm(system, prompt, max_tokens=4000, youtube_url=None, fast=False):
    text = "".join(c for c in llm_stream(system, prompt, max_tokens, youtube_url, fast) if isinstance(c, str))
    if not text.strip():
        raise RuntimeError("the AI returned an empty answer")
    return text


# ---------------------------------------------------------------- YouTube search
def youtube_search(query, n=10):
    key = cfg("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("YouTube API key is missing. Open Settings (⚙).")
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={"part": "snippet", "q": query, "type": "video", "maxResults": n,
                "order": "relevance", "videoEmbeddable": "true",
                "safeSearch": "moderate", "key": key},
        timeout=20,
    )
    r.raise_for_status()
    ids = [it["id"]["videoId"] for it in r.json().get("items", [])]
    if not ids:
        return []
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "snippet,contentDetails,statistics", "id": ",".join(ids), "key": key},
        timeout=20,
    )
    r.raise_for_status()
    by_id = {it["id"]: it for it in r.json().get("items", [])}

    videos = []
    for rank, vid in enumerate(ids):
        it = by_id.get(vid)
        if not it:
            continue
        secs = iso_duration_to_seconds(it["contentDetails"].get("duration"))
        if not (MIN_SECONDS <= secs <= MAX_SECONDS):
            continue
        sn, st = it["snippet"], it.get("statistics", {})
        videos.append({
            "id": vid, "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
            "description": (sn.get("description") or "")[:300],
            "published": sn.get("publishedAt", "")[:10],
            "duration": fmt_ts(secs), "seconds": secs,
            "views": int(st.get("viewCount", 0) or 0), "likes": int(st.get("likeCount", 0) or 0),
            "thumb": sn.get("thumbnails", {}).get("medium", {}).get("url", ""),
            "has_captions": it["contentDetails"].get("caption") == "true",
            "audio_lang": (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "").lower(),
            "yt_rank": rank,
        })
    return videos


def rerank(query, videos):
    """Ask the LLM which candidate is best for *studying* the topic."""
    if len(videos) <= 1:
        return videos
    lines = [
        f"{i}. {v['title']} | channel: {v['channel']} | {v['duration']} | "
        f"{v['views']:,} views | {v['likes']:,} likes | {v['published']}\n"
        f"   {v['description'][:200].replace(chr(10), ' ')}"
        for i, v in enumerate(videos)
    ]
    prompt = (
        f"A student wants to study: \"{query}\"\n\nCandidate YouTube videos:\n" + "\n".join(lines)
        + "\n\nRank them from best to worst for learning this topic: prefer clear, focused "
          "explanations from credible educators that match the exact topic; penalise clickbait, "
          'reaction videos and off-topic results. Reply with JSON only, like {"order": [3, 0, 1]}.'
    )
    try:
        try:
            out = llm("You rank educational videos. Output strict JSON only.", prompt, 300, fast=True)
        except Exception:  # ranking is optional: fall back to YouTube's order
            raise
        order = json.loads(re.search(r"\{.*\}", out, re.S).group(0))["order"]
        seen, ranked = set(), []
        for i in order:
            if isinstance(i, int) and 0 <= i < len(videos) and i not in seen:
                ranked.append(videos[i]); seen.add(i)
        return ranked + [v for i, v in enumerate(videos) if i not in seen]
    except Exception as e:  # fall back to YouTube's own order
        app.logger.warning("rerank failed: %s", e)
        return videos


# ---------------------------------------------------------------- transcripts
class BadTranscript(Exception):
    """Captions exist but are unusable (e.g. English speech auto-captioned as Hindi)."""


def latin_ratio(text):
    letters = [c for c in text if c.isalpha()]
    return sum(c.isascii() for c in letters) / len(letters) if letters else 1.0


def looks_garbled(video, segs, lang):
    """Ask the fast model whether auto-captions are real text or mis-recognised speech."""
    sample = " ".join(x["text"] for x in segs)[:1500]
    prompt = (f'Video title: "{video.get("title", "")}"\nAuto-generated captions (language code "{lang}"):\n'
              f"{sample}\n\nAre these captions coherent speech in that language that fits the title, or are they "
              "garbled speech recognition (for example English speech wrongly transcribed as Hindi, giving "
              'nonsense)? Reply with JSON only: {"garbled": true} or {"garbled": false}.')
    try:
        out = llm("You check transcript quality. Output strict JSON only.", prompt, 50, fast=True)
        return bool(json.loads(re.search(r"\{.*\}", out, re.S).group(0)).get("garbled"))
    except Exception as e:
        app.logger.info("garble check skipped: %s", e)
        return False  # can't tell: keep the captions


def captions_transcript(video):
    """YouTube's own captions. Prefers human-made ones; auto-captions are sanity-checked. Free, no key."""
    tl = ytt.list(video["id"])
    t = None
    for finder in (tl.find_manually_created_transcript, tl.find_generated_transcript):
        try:
            t = finder(TRANSCRIPT_LANGS)
            break
        except Exception:
            continue
    if t is None:
        t = next(iter(tl))  # whatever exists
    segs = [{"start": s.start, "text": s.text.replace("\n", " ").strip()}
            for s in t.fetch() if s.text.strip()]
    if not segs:
        raise RuntimeError("empty transcript")

    lang = t.language_code.split("-")[0].lower()
    if getattr(t, "is_generated", False):
        audio = video.get("audio_lang", "").split("-")[0]
        english_title = latin_ratio(video.get("title", "")) > 0.85
        suspicious = (audio and audio != lang) or (lang != "en" and english_title)
        if suspicious and looks_garbled(video, segs, lang):
            raise BadTranscript(f"YouTube's auto-captions ({lang}) are garbled")
    return segs, t.language_code + (" (auto)" if getattr(t, "is_generated", False) else "")


def whisper_transcript(video):
    """No captions? Download low-bitrate audio and transcribe with Groq Whisper (free tier)."""
    if not cfg("GROQ_API_KEY"):
        raise RuntimeError("no captions, and no Groq key for Whisper transcription")
    if video["seconds"] > int(cfg("MAX_WHISPER_SECONDS", "3600")):
        raise RuntimeError("no captions, and video too long for free Whisper transcription")
    import yt_dlp

    with tempfile.TemporaryDirectory() as tmp:
        opts = {"format": "worstaudio[ext=webm]/worstaudio[ext=m4a]/worstaudio",
                "outtmpl": f"{tmp}/a.%(ext)s", "quiet": True, "noprogress": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video['id']}"])
        audio = next(Path(tmp).glob("a.*"))
        if audio.stat().st_size > 25 * 1024 * 1024:
            raise RuntimeError("audio larger than Groq's 25 MB free upload limit")
        with open(audio, "rb") as f:
            r = post_with_retry(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {cfg('GROQ_API_KEY')}"},
                files={"file": (audio.name, f)},
                data={"model": cfg("WHISPER_MODEL", "whisper-large-v3-turbo"),
                      "response_format": "verbose_json"},
                timeout=600,
            )
    data = r.json()
    segs = [{"start": s["start"], "text": s["text"].strip()} for s in data.get("segments", [])]
    if not segs and data.get("text"):
        segs = [{"start": 0, "text": data["text"]}]
    return segs, (data.get("language") or "auto") + " (Whisper)"


def transcript_blocks(segs, block_seconds=30):
    """Group segments into ~30 s paragraphs, each prefixed with [mm:ss]."""
    out, cur, start = [], [], None
    for s in segs:
        if start is None:
            start = s["start"]
        cur.append(s["text"])
        if s["start"] - start >= block_seconds:
            out.append(f"[{fmt_ts(start)}] " + " ".join(cur))
            cur, start = [], None
    if cur:
        out.append(f"[{fmt_ts(start)}] " + " ".join(cur))
    return out


# ---------------------------------------------------------------- summary
SUMMARY_SYSTEM = """You are a study assistant. You turn a lecture/video into notes a student
can read INSTEAD of watching. Be accurate to what the speaker says; do not invent facts
(you may add a short clarifying note marked "(note)" where the speaker is ambiguous or
wrong). Use timestamps exactly in the form [mm:ss] or [h:mm:ss] so the student can jump there."""

SECTIONS = """Write the notes in {lang}, in Markdown, with exactly these sections:

## TL;DR
3-4 sentences: what the video teaches and the single most important takeaway.

## Key concepts
Bullet list of the core ideas / definitions / formulas, each with a timestamp.

## Detailed notes
Section-by-section notes following the video's flow. Use ### sub-headings with a
timestamp, e.g. "### Gradient descent intuition [04:12]". Include worked examples,
steps and any formulas. Write all maths in LaTeX: $...$ inline and $$...$$ on its own
line for display equations (never put $$ inside a sentence).

## Quick revision questions
5 short questions with answers (answer in italics on the next line).

## Worth watching
1-3 moments where watching beats reading (diagrams, demos), with timestamps."""


def notes_prompt(query, video, blocks, out_lang, use=None):
    text = "\n".join(blocks)
    budget = CHAR_BUDGET[use or provider()]
    truncated = len(text) > budget
    return (f'Student\'s search: "{query}"\n'
            f"Video: \"{video['title']}\" by {video['channel']} ({video['duration']})\n"
            + ("(Transcript truncated for length; summarise what is provided and say so in the TL;DR.)\n"
               if truncated else "")
            + "\n" + SECTIONS.format(lang=out_lang) + "\n\nTRANSCRIPT:\n" + text[:budget])


def video_prompt(query, video, out_lang):
    return (f'Student\'s search: "{query}"\nThis is the video "{video["title"]}" by '
            f"{video['channel']} ({video['duration']}). It has no transcript available, so "
            "base the notes on the video's audio and visuals.\n\n" + SECTIONS.format(lang=out_lang))


EXPECTED_NOTES_CHARS = 6500   # typical length of the notes; drives the progress bar


def ev(kind, **data):
    return json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n"


def video_events(query, video, out_lang, alternatives, redo=False):
    """Yield progress events for one video. Returns True when notes were produced."""
    yield ev("video", video=video, alternatives=alternatives)
    key = h("v3", video["id"], out_lang, provider())
    cached = None if redo else cache_get(key)
    if cached:
        yield ev("stage", pct=100, step="notes", msg="Loaded from your saved notes")
        yield ev("transcript", blocks=cached["transcript"], lang=cached["transcript_lang"])
        yield ev("done", result=cached)
        return True

    blocks, lang, youtube_url = None, None, None
    if redo:
        yield ev("stage", pct=30, step="transcript", msg="Ignoring YouTube's captions and transcribing again…")
    else:
        yield ev("stage", pct=30, step="transcript", msg="Reading the video's captions…")
        try:
            segs, lang = captions_transcript(video)
            blocks = transcript_blocks(segs)
        except BadTranscript as e:
            yield ev("stage", pct=32, step="transcript", msg=f"{e}, so they won't be used. Getting a proper transcript…")
        except Exception as e:
            app.logger.info("captions failed for %s: %s", video["id"], e)

    if blocks is None and cfg("GROQ_API_KEY"):
        yield ev("stage", pct=34, step="transcript",
                 msg=f"Transcribing the audio with Whisper ({video['duration']} video, may take a minute)…")
        try:
            segs, lang = whisper_transcript(video)
            blocks = transcript_blocks(segs)
        except RateLimited:
            raise
        except Exception as e:
            app.logger.info("whisper failed for %s: %s", video["id"], e)

    if blocks is None:
        if provider() == "gemini" and cfg("GEMINI_VIDEO_FALLBACK", "1") == "1":
            youtube_url = f"https://www.youtube.com/watch?v={video['id']}"
            lang = "none (Gemini watched the video)"
            blocks = []
            yield ev("stage", pct=40, step="notes", msg="No usable transcript. Gemini is watching the video itself (slower)…")
        else:
            yield ev("skip", msg="No usable transcript for this video. Trying the next best one…")
            return False

    yield ev("transcript", blocks=blocks, lang=lang)
    yield ev("stage", pct=45, step="notes", msg=f"Writing your notes with {provider().title()}…")

    notes, last_pct = "", 45

    def write_with(use):
        nonlocal notes, last_pct
        prompt = video_prompt(query, video, out_lang) if youtube_url else notes_prompt(query, video, blocks, out_lang, use)
        for chunk in llm_stream(SUMMARY_SYSTEM, prompt, max_tokens=8000, youtube_url=youtube_url, use=use):
            if isinstance(chunk, dict):
                yield ev("stage", pct=None, step="notes", msg=chunk["switch"])
                continue
            notes += chunk
            pct = min(97, 45 + int(52 * len(notes) / EXPECTED_NOTES_CHARS))
            yield ev("notes", delta=chunk, pct=pct if pct != last_pct else None)
            last_pct = pct

    try:
        yield from write_with(provider())
    except ModelBusy:
        # Gemini overloaded: hand over to Groq if possible (needs a transcript, not video mode)
        if provider() == "gemini" and cfg("GROQ_API_KEY") and not youtube_url and not notes:
            yield ev("stage", pct=46, step="notes", msg="Gemini's free servers are busy. Writing the notes with Groq instead…")
            yield from write_with("groq")
        else:
            raise
    if not notes.strip():
        raise RuntimeError("the AI returned empty notes")

    result = {"video": video, "transcript": blocks, "transcript_lang": lang, "summary_md": notes,
              "query": query or video["title"], "lang": out_lang, "saved_at": time.time(),
              "alternatives": alternatives, "key": key}
    cache_put(key, result)
    yield ev("done", result=result)
    return True


def search_events(query, out_lang):
    t0 = time.time()
    try:
        skey = h("search", query, provider())
        candidates = cache_get(skey)
        if candidates is None:
            yield ev("stage", pct=5, step="search", msg="Searching YouTube…")
            found = youtube_search(query)
            if not found:
                yield ev("error", msg="No suitable videos found. Try different words.")
                return
            yield ev("stage", pct=15, step="pick",
                     msg=f"Found {len(found)} videos. Picking the best one for studying…")
            candidates = rerank(query, found)
            cache_put(skey, candidates)

        for v in candidates[:4]:
            alts = [c for c in candidates if c["id"] != v["id"]][:5]
            ok = yield from video_events(query, v, out_lang, alts)
            if ok:
                app.logger.info("search %r done in %.1fs", query, time.time() - t0)
                return
        yield ev("error", msg="Found videos, but couldn't get a transcript for any of them.")
    except RateLimited as e:
        yield ev("error", msg=str(e), retry=True)
    except ModelBusy as e:
        yield ev("error", msg=busy_msg(e), retry=True)
    except requests.HTTPError as e:
        yield ev("error", msg=f"YouTube API error {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        app.logger.exception("search failed")
        yield ev("error", msg=f"{type(e).__name__}: {e}")


def single_video_events(query, video, out_lang, alternatives, redo=False):
    try:
        ok = yield from video_events(query, video, out_lang, alternatives, redo)
        if not ok:
            yield ev("error", msg="This video has no transcript, so it can't be summarised.")
    except RateLimited as e:
        yield ev("error", msg=str(e), retry=True)
    except ModelBusy as e:
        yield ev("error", msg=busy_msg(e), retry=True)
    except Exception as e:
        app.logger.exception("video failed")
        yield ev("error", msg=f"Could not summarise this video: {e}")


def busy_msg(e):
    tip = "" if cfg("GROQ_API_KEY") else " Tip: add a free Groq key in Settings and StudyTube will switch to Groq automatically when Gemini is busy."
    return "Gemini's free servers are overloaded right now (this is on Google's side). Try again in a minute." + tip


# ---------------------------------------------------------------- transcript translation
TS_LINE = re.compile(r"^\s*\[((?:\d{1,2}:)?\d{1,2}:\d{2})\]\s*(.*)$")
TRANSLATE_CHUNK_CHARS = int(cfg("TRANSLATE_CHUNK_CHARS", "9000"))

TRANSLATE_SYSTEM = """You translate lecture transcripts into clear, natural English.
Each input line starts with a timestamp like [12:34]. Output exactly one line per input line,
starting with the SAME timestamp, followed by its English translation. Keep technical terms,
formulas, names and numbers accurate; fix obvious speech-recognition slips only when the meaning
is clear. Lines already in English (or Hinglish technical terms) are kept as they are.
No commentary, no headings, nothing else."""


def is_english(lang):
    return bool(re.match(r"^(en|english)\b", (lang or "").strip().lower()))


def translate_chunks(blocks):
    chunks, cur, size = [], [], 0
    for b in blocks:
        if cur and size + len(b) > TRANSLATE_CHUNK_CHARS:
            chunks.append(cur); cur, size = [], 0
        cur.append(b); size += len(b) + 1
    if cur:
        chunks.append(cur)
    return chunks


def translate_events(blocks, src_lang, key):
    try:
        if key and KEY_RE.match(key):
            saved = cache_get(key)
            if saved and saved.get("transcript_en") and len(saved["transcript_en"]) == len(blocks):
                yield ev("done", blocks=saved["transcript_en"], cached=True)
                return
        chunks = translate_chunks(blocks)
        out, done_lines = [], 0
        yield ev("progress", pct=2, msg=f"Translating {len(blocks)} lines from {src_lang} to English…")
        for i, chunk in enumerate(chunks):
            prompt = f"Source language: {src_lang}\n\n" + "\n".join(chunk)
            got = {}

            def absorb(text):
                for line in text.splitlines():
                    m = TS_LINE.match(line)
                    if m and m.group(2).strip():
                        got.setdefault(m.group(1), m.group(2).strip())

            absorb(llm(TRANSLATE_SYSTEM, prompt, max_tokens=8000))
            missing = [b for b in chunk if (TS_LINE.match(b) or [None, None])[1] not in got]
            if missing and len(missing) < len(chunk):   # model skipped some lines: one retry for just those
                absorb(llm(TRANSLATE_SYSTEM, f"Source language: {src_lang}\n\n" + "\n".join(missing), max_tokens=4000))
            # align by timestamp; anything still missing keeps its original line
            for b in chunk:
                m = TS_LINE.match(b)
                if m and m.group(1) in got:
                    out.append(f"[{m.group(1)}] {got[m.group(1)]}")
                else:
                    out.append(b)
            done_lines += len(chunk)
            yield ev("progress", pct=int(100 * done_lines / len(blocks)),
                     msg=f"Translated {done_lines} of {len(blocks)} lines…", partial=out)
        if key and KEY_RE.match(key):
            saved = cache_get(key)
            if saved:
                saved["transcript_en"] = out
                cache_put(key, saved)
        yield ev("done", blocks=out)
    except RateLimited as e:
        yield ev("error", msg=str(e))
    except ModelBusy as e:
        yield ev("error", msg=busy_msg(e))
    except Exception as e:
        app.logger.exception("translate failed")
        yield ev("error", msg=f"Translation failed: {e}")


@app.post("/api/translate")
def api_translate():
    b = request.get_json(force=True)
    blocks = [x for x in (b.get("blocks") or []) if isinstance(x, str)]
    if not blocks:
        return jsonify(error="Nothing to translate."), 400
    return stream(translate_events(blocks, b.get("lang") or "the original language", b.get("key") or ""))


# ---------------------------------------------------------------- history (saved notes)
KEY_RE = re.compile(r"^[0-9a-f]{16}$")


def history_items(limit=200):
    items = []
    for f in CACHE_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict) or "summary_md" not in d:
            continue  # search-result caches, not notes
        v = d.get("video", {})
        items.append({"key": f.stem, "query": d.get("query") or v.get("title", ""),
                      "title": v.get("title", ""), "channel": v.get("channel", ""),
                      "duration": v.get("duration", ""), "thumb": v.get("thumb", ""),
                      "video_id": v.get("id", ""), "lang": d.get("lang", "English"),
                      "saved_at": d.get("saved_at") or f.stat().st_mtime})
    items.sort(key=lambda x: x["saved_at"], reverse=True)
    return items[:limit]


@app.get("/api/history")
def api_history():
    return jsonify(history_items())


@app.get("/api/history/<key>")
def api_history_open(key):
    if not KEY_RE.match(key):
        return jsonify(error="bad key"), 400
    d = cache_get(key)
    if not d:
        return jsonify(error="These notes are no longer saved."), 404
    d.setdefault("query", d.get("video", {}).get("title", ""))
    d.setdefault("alternatives", [])
    return jsonify(d)


@app.delete("/api/history/<key>")
def api_history_delete(key):
    if not KEY_RE.match(key):
        return jsonify(error="bad key"), 400
    f = CACHE_DIR / f"{key}.json"
    if f.exists():
        f.unlink()
    return jsonify(ok=True)


def stream(gen):
    return Response(stream_with_context(gen), mimetype="application/x-ndjson",
                    headers={"X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- routes
@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/search")
def api_search():
    body = request.get_json(force=True)
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify(error="Type something to search."), 400
    return stream(search_events(query, body.get("lang") or "English"))


@app.post("/api/video")
def api_video():
    """Summarise a specific alternative the user clicked."""
    body = request.get_json(force=True)
    return stream(single_video_events(body.get("query", ""), body["video"],
                                      body.get("lang") or "English", body.get("alternatives") or [],
                                      bool(body.get("redo"))))


# ---------------------------------------------------------------- settings
KEY_FIELDS = {"youtube_key": "YOUTUBE_API_KEY", "gemini_key": "GEMINI_API_KEY",
              "groq_key": "GROQ_API_KEY", "anthropic_key": "ANTHROPIC_API_KEY"}


def save_env(updates):
    """Write key=value pairs into .env (keeping other lines) and apply them now."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    done = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if k in updates:
            lines[i] = f"{k}={updates[k]}"
            done.add(k)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ.update(updates)


APP_VERSION = "4.5"


def settings_state():
    p = provider()
    missing = []
    if not cfg("YOUTUBE_API_KEY"):
        missing.append("YouTube API key")
    if not provider_has_key(p):
        missing.append({"gemini": "Gemini key (or a YouTube key that also allows Gemini)",
                        "groq": "Groq key", "anthropic": "Anthropic key"}.get(p, p))
    return dict(provider=p, ollama_model=cfg("OLLAMA_MODEL", "llama3.1"),
                has={f: bool(cfg(env)) for f, env in KEY_FIELDS.items()},
                missing=missing, ready=not missing, version=APP_VERSION,
                env_file=str(ENV_FILE))


@app.after_request
def no_cache(resp):
    # The app window must never show an old copy of the page after an update.
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/settings")
def get_settings():
    return jsonify(settings_state())


@app.post("/api/settings")
def post_settings():
    b = request.get_json(force=True)
    upd = {env: b[f].strip() for f, env in KEY_FIELDS.items() if (b.get(f) or "").strip()}
    if b.get("provider") in PROVIDERS:
        upd["LLM_PROVIDER"] = b["provider"]
    if (b.get("ollama_model") or "").strip():
        upd["OLLAMA_MODEL"] = b["ollama_model"].strip()
    if upd:
        try:
            save_env(upd)
        except OSError as e:
            os.environ.update(upd)  # still works for this session
            return jsonify(settings_state() | {"save_error": f"Could not write {ENV_FILE}: {e}. "
                                               "Move the StudyTube folder to Documents and reinstall."})
    return jsonify(settings_state())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(cfg("PORT", "5000")), debug=False)
