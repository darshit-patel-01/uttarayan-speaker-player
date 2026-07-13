"""
Run this to diagnose YouTube 403 errors.
Usage:  python test_download.py
"""
import os
import yt_dlp

URL = "https://youtu.be/gCYcHz2k5x0"   # the song that was failing

print("=" * 60)
print(f"yt-dlp version: {yt_dlp.version.__version__}")

cookies_file = os.path.join(os.path.dirname(__file__), "cookies.txt")
if os.path.exists(cookies_file):
    print(f"cookies.txt: FOUND ({os.path.getsize(cookies_file)} bytes)")
else:
    print("cookies.txt: NOT FOUND")

print("=" * 60)

clients = ["ios", "android", "web"]
for client in clients:
    print(f"\nTrying player_client={client} ...")
    opts = {
        "format": "bestaudio/best",
        "quiet": False,
        "no_warnings": False,
        "noplaylist": True,
        "skip_download": True,          # just check URL, don't actually download
        "extractor_args": {"youtube": {"player_client": [client]}},
        **({"cookiefile": cookies_file} if os.path.exists(cookies_file) else {}),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(URL, download=False)
            print(f"  ✓ {client} worked — title: {info.get('title', '?')}")
            break
    except Exception as e:
        print(f"  ✗ {client} failed: {e}")
else:
    print("\nAll clients failed. Try:  venv\\Scripts\\pip install -U yt-dlp")
