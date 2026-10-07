#!/usr/bin/env python3
"""videodl - download videos from a website.

Strategy (first that works wins):
  1. yt-dlp (if installed): supports 1000+ sites, HLS/DASH, playlists.
  2. Built-in fallback (stdlib only):
       - direct video file URLs (.mp4, .webm, ...) with resume + progress
       - web pages: scan HTML for <video>/<source>/og:video/.mp4/.m3u8 links
       - HLS (.m3u8) streams via ffmpeg, if available

Only download content you own or are permitted to download.
"""
import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
VIDEO_EXT = (".mp4", ".webm", ".mkv", ".mov", ".m4v", ".flv", ".avi", ".ts")
URL_RE = re.compile(r"""https?:(?:\\?/){2}[^\s"'<>\\]+?\.(?:mp4|webm|mkv|mov|m4v|m3u8)[^\s"'<>\\]*""", re.I)


def request(url, referer=None, headers=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        h["Referer"] = referer
    h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)


def is_direct(url):
    path = urllib.parse.urlparse(url).path.lower()
    return path.endswith(VIDEO_EXT) or path.endswith(".m3u8")


def find_video_urls(page_url, extra_headers=None):
    """Scrape a page for candidate media URLs, best candidates first."""
    with request(page_url, headers=extra_headers) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        text = r.read().decode(charset, errors="replace")
    found = []
    for pat in (
        r'<meta[^>]+property=["\']og:video(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+)',
        r'<(?:video|source)[^>]+src=["\']([^"\']+)',
        r'["\']?(?:contentUrl|file|src|url)["\']?\s*[:=]\s*["\']([^"\']+\.(?:mp4|webm|m3u8)[^"\']*)',
    ):
        found += re.findall(pat, text, re.I)
    found += URL_RE.findall(text)
    out, seen = [], set()
    for u in found:
        u = html.unescape(u.replace("\\/", "/"))
        u = urllib.parse.urljoin(page_url, u)
        if u.startswith("http") and u not in seen and is_direct(u):
            seen.add(u)
            out.append(u)
    # prefer progressive files over HLS (simpler, no ffmpeg needed)
    out.sort(key=lambda u: urllib.parse.urlparse(u).path.lower().endswith(".m3u8"))
    return out


def filename_from(url, resp, outdir):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
    name = urllib.parse.unquote(m.group(1)) if m else os.path.basename(
        urllib.parse.urlparse(url).path) or "video.mp4"
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return os.path.join(outdir, name)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def download_file(url, outdir, referer=None, name=None, quiet=False, headers=None):
    os.makedirs(outdir, exist_ok=True)
    with request(url, referer=referer, headers=headers) as probe:
        path = os.path.join(outdir, name) if name else filename_from(url, probe, outdir)
        total = int(probe.headers.get("Content-Length") or 0)
        ranges = "bytes" in probe.headers.get("Accept-Ranges", "")
    part = path + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    hdrs = dict(headers or {})
    mode = "wb"
    if have and ranges and (not total or have < total):
        hdrs["Range"] = f"bytes={have}-"
        mode = "ab"
    else:
        have = 0
    with request(url, referer=referer, headers=hdrs) as r, open(part, mode) as f:
        if mode == "ab" and r.status != 206:  # server ignored Range
            f.seek(0)
            f.truncate()
            have = 0
        done = have
        while chunk := r.read(1 << 16):
            f.write(chunk)
            done += len(chunk)
            if not quiet:
                pct = f" {done * 100 // total}%" if total else ""
                sys.stderr.write(f"\r  {human(done)}{' / ' + human(total) if total else ''}{pct}   ")
        if not quiet:
            sys.stderr.write("\n")
    os.replace(part, path)
    return path


def download_hls(url, outdir, referer=None, name=None):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("HLS stream found but ffmpeg is not installed")
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, name or "video.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stats", "-user_agent", UA]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\n"]
    cmd += ["-i", url, "-c", "copy", "-bsf:a", "aac_adtstoasc", out]
    subprocess.run(cmd, check=True)
    return out


def fetch_one(url, args):
    """Built-in fallback download. Returns list of saved paths."""
    hdrs = dict(h.split(":", 1) for h in args.header) if args.header else None
    hdrs = {k.strip(): v.strip() for k, v in (hdrs or {}).items()} or None
    if is_direct(url):
        candidates, referer = [url], args.referer
    else:
        candidates, referer = find_video_urls(url, hdrs), args.referer or url
        if not candidates:
            raise RuntimeError("no video links found in page (it may load video via JavaScript; "
                               "install yt-dlp for broader support)")
        if args.list:
            for c in candidates:
                print(c)
            return []
        if not args.all:
            candidates = candidates[:1]
    saved = []
    for c in candidates:
        print(f"Downloading {c}", file=sys.stderr)
        if urllib.parse.urlparse(c).path.lower().endswith(".m3u8"):
            saved.append(download_hls(c, args.output, referer, args.name))
        else:
            saved.append(download_file(c, args.output, referer, args.name, headers=hdrs))
    return saved


def run_ytdlp(url, args):
    """Try yt-dlp via module or binary. Returns True on success, None if unavailable."""
    base = [
        "-o", os.path.join(args.output, args.name or "%(title)s [%(id)s].%(ext)s"),
        "-f", args.format,
    ]
    if args.audio_only:
        base += ["-x", "--audio-format", "mp3"]
    if args.referer:
        base += ["--referer", args.referer]
    for h in args.header or []:
        base += ["--add-header", h]
    if args.cookies:
        base += ["--cookies", args.cookies]
    if args.cookies_from_browser:
        base += ["--cookies-from-browser", args.cookies_from_browser]
    if not args.playlist:
        base += ["--no-playlist"]
    if args.list:
        base += ["-F"]
    if shutil.which("yt-dlp"):
        cmd = ["yt-dlp"]
    else:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return None
        cmd = [sys.executable, "-m", "yt_dlp"]
    return subprocess.run(cmd + base + [url]).returncode == 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Download videos from a website.")
    p.add_argument("urls", nargs="+", help="page or direct video URL(s)")
    p.add_argument("-o", "--output", default="downloads", help="output directory (default: downloads)")
    p.add_argument("-n", "--name", help="output filename (single URL)")
    p.add_argument("-f", "--format", default="bv*+ba/b", help="yt-dlp format selector")
    p.add_argument("-x", "--audio-only", action="store_true", help="extract audio as mp3 (yt-dlp)")
    p.add_argument("--playlist", action="store_true", help="allow playlist download (yt-dlp)")
    p.add_argument("--referer", help="Referer header")
    p.add_argument("-H", "--header", action="append", help='extra header, e.g. -H "Cookie: a=b"')
    p.add_argument("--cookies", help="Netscape cookies file (yt-dlp)")
    p.add_argument("--cookies-from-browser", help="e.g. firefox, chrome (yt-dlp)")
    p.add_argument("--list", action="store_true", help="list found video URLs/formats, don't download")
    p.add_argument("--all", action="store_true", help="fallback: download every video found on page")
    p.add_argument("--no-ytdlp", action="store_true", help="skip yt-dlp, use built-in fallback only")
    args = p.parse_args(argv)
    if args.name and len(args.urls) > 1:
        p.error("--name only works with a single URL")

    failures = 0
    for url in args.urls:
        if "://" not in url:
            url = "https://" + url  # bare domain
        try:
            ok = None if args.no_ytdlp or is_direct(url) else run_ytdlp(url, args)
            if ok:
                continue
            if ok is False:
                print("yt-dlp failed; trying built-in fallback...", file=sys.stderr)
            for path in fetch_one(url, args):
                print(f"Saved: {path}")
        except (RuntimeError, urllib.error.URLError, subprocess.CalledProcessError, OSError) as e:
            print(f"error: {url}: {e}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
