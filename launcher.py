"""Desktop launcher for ekagra (formerly StudyTube).

Starts the local server on a free port and opens it in its own app window
(pywebview). If that is not possible, it opens an Edge/Chrome "app mode" window,
and as a last resort the default browser.
"""
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
os.chdir(BASE)

# pythonw.exe has no console: send output to a log file instead
if sys.stdout is None or sys.stderr is None:
    log = open(BASE / "studytube.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(levelname)s %(message)s")

import winshell  # noqa: E402
winshell.set_app_id()  # must happen before any window exists

T0 = time.time()
# NOTE: the engine (app.py: Flask, YouTube libraries...) is imported in a background thread,
# *after* the window is up, so the splash appears at once instead of after the heavy imports.


def start_engine(port):
    def run():
        from app import app
        logging.info("engine imported after %.1fs", time.time() - T0)
        app.run(host="127.0.0.1", port=port, use_reloader=False)
    threading.Thread(target=run, daemon=True).start()


def free_port(preferred=5000):
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue


def wait_until_up(url, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url + "api/settings", timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def browser_app_window(url):
    """Edge / Chrome / Chromium in --app mode: looks like a standalone window."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ] + [shutil.which(n) or "" for n in
         ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
    for exe in candidates:
        if exe and Path(exe).exists():
            profile = BASE / ".browser-profile"
            return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}",
                                     "--window-size=1400,900", "--no-first-run"])
    return None


def chosen_theme():
    """THEME from .env (system | light | dark); 'system' asks Windows whether apps use dark mode."""
    theme = "system"
    try:
        for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("THEME="):
                theme = line.split("=", 1)[1].strip().lower() or "system"
    except OSError:
        pass
    if theme in ("light", "dark"):
        return theme
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
        except OSError:
            pass
    return "dark"


def chosen_ui():
    """UI_LANG from .env: en (default) or hi."""
    try:
        for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("UI_LANG="):
                return "hi" if line.split("=", 1)[1].strip().lower() == "hi" else "en"
    except OSError:
        pass
    return "en"


THEME_COLORS = {
    "dark": {"bg": "#161614", "ink": "#ecebe6", "muted": "#9c9a92", "accent": "#F7931E", "gif": "splash.gif"},
    "light": {"bg": "#F6F5F1", "ink": "#12263F", "muted": "#5B5853", "accent": "#C2410C", "gif": "splash-light.gif"},
}


def splash_html(theme):
    import base64
    c = THEME_COLORS[theme]
    gif_path = BASE / "static" / c["gif"]
    if not gif_path.exists():
        gif_path = BASE / "static" / "splash.gif"
    gif = base64.b64encode(gif_path.read_bytes()).decode()
    wm_path = BASE / "static" / "fonts" / "ekagra-wordmark.woff"
    wm = base64.b64encode(wm_path.read_bytes()).decode() if wm_path.exists() else ""
    hi = chosen_ui() == "hi"
    hi_path = BASE / "static" / "fonts" / "ekagra-wordmark-hi.woff"
    wmh = base64.b64encode(hi_path.read_bytes()).decode() if hi and hi_path.exists() else ""
    lang_attr, h1cls = ("hi", "hi") if hi else ("en", "")
    if hi:
        name, sub, tag, wait = "एकाग्र", "केंद्रित अध्ययन, जिज्ञासा से महारत तक।", "पढ़ाई करें, स्क्रॉल नहीं।", "तैयारी हो रही है"
    else:
        name, sub, tag, wait = "ekagra", "Focused learning, from curiosity to mastery.", "Study, not scroll.", "Getting things ready"
    return f"""<!DOCTYPE html><html lang="{lang_attr}"><head><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;background:{c["bg"]};color:{c["ink"]};font:15px system-ui,"Segoe UI",sans-serif;
  display:flex;align-items:center;justify-content:center;user-select:none;overflow:hidden}}
.c{{text-align:center}} img{{width:160px;height:160px}}
@font-face{{font-family:"ekagraWM";src:url(data:font/woff;base64,{wm}) format("woff");font-weight:800}}
@font-face{{font-family:"ekagraHI";src:url(data:font/woff;base64,{wmh}) format("woff");font-weight:800}}
h1{{font:800 34px "ekagraWM",system-ui,sans-serif;margin:14px 0 4px;letter-spacing:-.01em}}
h1.hi{{font:800 38px "ekagraHI","Nirmala UI",sans-serif;letter-spacing:0}} h1 span{{color:{c["accent"]}}}
.sub{{color:{c["muted"]};font-size:13px;margin:0 0 10px}}
.tag{{color:{c["ink"]};font-weight:600;font-size:16px;margin:0 0 14px}}
p{{color:{c["muted"]};margin:0}} .dots::after{{content:"";animation:d 1.2s steps(4) infinite}}
@keyframes d{{0%{{content:""}}25%{{content:"."}}50%{{content:".."}}75%{{content:"..."}}}}
</style></head><body><div class="c"><img src="data:image/gif;base64,{gif}" alt="">
<h1 class="{h1cls}">{name}<span>.</span></h1><p class="sub">{sub}</p><p class="tag">{tag}</p><p>{wait}<span class="dots"></span></p></div></body></html>"""


SPLASH_MIN_SECONDS = 2.0   # one full page-turn of the animation


def main():
    port = free_port(int(os.getenv("PORT", "5000")))
    url = f"http://127.0.0.1:{port}/"

    # 1) native window: splash first, engine loads behind it
    try:
        import webview
        theme = chosen_theme()
        window = webview.create_window("ekagra", html=splash_html(theme), width=1400, height=900,
                                       min_size=(900, 600), background_color=THEME_COLORS[theme]["bg"],
                                       text_select=True, zoomable=True)
        splash_painted = threading.Event()

        def on_loaded():
            if not splash_painted.is_set():
                logging.info("splash on screen after %.1fs", time.time() - T0)
                splash_painted.set()

        window.events.loaded += on_loaded
        window.events.shown += winshell.apply_window_icon

        def boot():
            start_engine(port)
            splash_painted.wait(timeout=15)            # the animation is really visible now
            shown_at = time.time()
            ok = wait_until_up(url, timeout=40)
            time.sleep(max(0.0, SPLASH_MIN_SECONDS - (time.time() - shown_at)))
            if ok:
                logging.info("app ready after %.1fs", time.time() - T0)
                window.load_url(url)
            else:
                window.load_html("<body style='background:#161614;color:#f87171;font:16px system-ui;"
                                 "padding:40px'>ekagra's engine did not start. See studytube.log "
                                 "in the app folder.</body>")
            winshell.apply_window_icon()   # again, in case the first call was too early

        # On Windows the icon is applied by winshell (the window component rejects PNG icons
        # with an uncatchable .NET error); the icon= option is only for Linux/macOS.
        if sys.platform == "win32":
            webview.start(boot)
        else:
            webview.start(boot, icon=str(BASE / "icon.png"))
        return  # window closed -> app exits
    except Exception as e:
        logging.warning("pywebview unavailable (%s); falling back", e)

    start_engine(port)
    if not wait_until_up(url, timeout=40):
        logging.error("server did not start")
        sys.exit(1)

    # 2) Edge/Chrome app window - server lives as long as that window
    proc = browser_app_window(url)
    if proc:
        proc.wait()
        return

    # 3) default browser - keep serving until killed
    webbrowser.open(url)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
