# videodl

Command-line tool to download videos from websites.

- Uses **yt-dlp** when installed (`pip install yt-dlp`) for YouTube, Vimeo, X, etc.
- Falls back to a **stdlib-only** downloader for direct `.mp4/.webm/...` links and for pages that
  embed `<video>`/`og:video`/`.m3u8` links. HLS needs `ffmpeg`.

```bash
python3 videodl.py https://example.com/page-with-video
python3 videodl.py https://example.com/clip.mp4 -o ~/Videos -n clip.mp4
python3 videodl.py URL --list            # show found video URLs / formats
python3 videodl.py URL -x                # audio only (yt-dlp)
python3 videodl.py URL --referer https://example.com -H "Cookie: a=b"
python3 videodl.py URL1 URL2 --no-ytdlp  # built-in fallback only
```

Downloads resume (`.part` files) for direct links when the server supports ranges.
Pages that build their player with JavaScript or use DRM won't work with the fallback
(and DRM-protected video is not supported at all). Only download content you have the right to.
