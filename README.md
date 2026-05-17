# World Media

![License: MIT](https://img.shields.io/github/license/aivrar/world-media)
![Platform](https://img.shields.io/badge/platform-cross--compatible-blue)
![Built on](https://img.shields.io/badge/Built%20on-portable--linux--in--a--box-7d56f4)
![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04-E95420)
![Python](https://img.shields.io/badge/Python-3.12-3776AB)
![No telemetry](https://img.shields.io/badge/telemetry-none-success)
![No accounts](https://img.shields.io/badge/signup-not%20required-success)
![Bundle](https://img.shields.io/badge/runtime-heavier-orange)

**World Media is the heavier cross-compatible/Linux-runtime build of the World
Media app.** It packages a tiny Linux environment with the web UI and proxy so
the same app model can run across the portable Linux-in-a-box runtime line.

![World Media - Library view](screenshots/1.PNG)

## Related Builds

Choose the build that matches what you need:

| Build | Repo | Best For | Tradeoff |
|---|---|---|---|
| **World Media** | `aivrar/world-media` | The heavier cross-compatible/Linux-runtime build | Larger download and runtime footprint |
| **World Media Windows** | [aivrar/WorldMediaWindows](https://github.com/aivrar/WorldMediaWindows) | Windows users who want the smallest, simplest `.exe` | Windows-only |

## Install

1. Download **[World Media.exe](../../releases/latest/download/World.Media.exe)**
   from the latest [Release](../../releases).
2. Double-click it.

First launch self-extracts the app into `%LOCALAPPDATA%\WorldMedia\`, imports a
pre-baked Ubuntu image into the private app runtime, and opens the WebView.
Later launches are much faster.

Windows users who do not need the Linux-runtime build should use the lighter
[World Media Windows](https://github.com/aivrar/WorldMediaWindows) repo.

## What You Get

Five tabs across the top: **Library**, **Tuner**, **Grid**, **Discovery**, and
**About**.

**Library** - search and browse everything. Left sidebar groups results by type
and source: Radio Browser, iptv-org, Internet Archive, NASA, Wikimedia Commons,
and LibriVox.

![Library - TV channels with sidebar counts](screenshots/1.PNG)

**Tuner** - a radio-style dial for live radio and live TV. Drag the dial or use
arrow keys; each station gets a cosmetic frequency.

![Tuner - analog dial](screenshots/2.PNG)

**Library detail panel** - click any item to see metadata, license, source, and
play it.

![Library - detail panel with Wikimedia Commons video](screenshots/3.PNG)

**Grid** - TV-guide-style tiles for live radio and live TV.

**Discovery** - random open media from the enabled sources.

## Playback

Video plays in a movable overlay with fullscreen support.

![Fullscreen TV - live IPTV from iptv-org](screenshots/4.PNG)

![Fullscreen video - NASA Image and Video Library](screenshots/5.PNG)

## Where The Content Comes From

Every item World Media surfaces comes from one of six public, freely accessible
archives. The app does not host content and does not require API keys.

| Source | What it provides | Home | Licensing |
|---|---|---|---|
| [Radio Browser](https://www.radio-browser.info) | Internet radio stations | `radio-browser.info` | Stations retain their own broadcast rights |
| [iptv-org](https://iptv-org.github.io) | Free-to-air IPTV channels | `iptv-org.github.io` | Stream operators retain their own rights |
| [Internet Archive](https://archive.org) | Films, recordings, books, and other media | `archive.org` | Per item, often public domain or Creative Commons |
| [NASA Image and Video Library](https://images.nasa.gov) | Mission photos, videos, and audio | `images.nasa.gov` | Public domain for U.S. government work |
| [Wikimedia Commons](https://commons.wikimedia.org) | Free-licensed media files | `commons.wikimedia.org` | CC-BY-SA or public domain per file |
| [LibriVox](https://librivox.org) | Public-domain audiobooks | `librivox.org` | Public domain |

## Privacy And Isolation

- **No accounts.** Nothing to sign up for.
- **No telemetry.** The app does not collect usage data.
- **No API keys.** All six sources use public anonymous endpoints.
- **Same-origin proxy with allowlist.** Some sources block browser requests for
  CORS reasons. The bundled Python proxy forwards only to the hard-coded public
  media hosts. Stream URLs are played directly and are not proxied.
- **Private runtime.** App processes run inside a private app-owned Linux
  runtime. Runtime state is generated locally and is not committed to this repo.

## Requirements

- Windows 10 version 2004 or newer, or Windows 11 for the current packaged exe
- WSL2 installed for the Windows Linux-runtime package
- Microsoft Edge WebView2 Runtime
- About 200 MB free disk space for the exe and extracted runtime
- Internet access for upstream catalogs and streams

For Windows users who do not need the Linux-runtime package, the lighter
[World Media Windows](https://github.com/aivrar/WorldMediaWindows) build avoids
WSL and is much smaller.

## How It Works

```text
World Media.exe
  -> self-extracts to %LOCALAPPDATA%\WorldMedia\
  -> starts the inner launcher
  -> imports the bundled Linux image as the private app runtime
  -> runs server.py inside that runtime
  -> opens a WebView2 window pointed at the local app server
```

The runtime contains:

```text
server.py          # static frontend server and CORS-bypass proxy
frontend/          # built HTML/CSS/JS bundle
linux/rootfs.tar.gz
linux/ubuntu-base.tar.gz
linux/bzImage
linux/initramfs.cpio.gz
linux/bbl64.bin
linux/rootfs-riscv64.ext2
```

## Build From Source

Frontend source currently lives in the sibling app tree:

```text
E:\World_media_app\world-media
```

To rebuild and repack:

```bash
# In the frontend tree:
npm run build

# Sync into this repo's frontend/ dir:
bash sync-frontend.sh

# Build the single shareable exe in the sibling WorldMedia_Single tree:
# 1. 7-zip this repo into bundle.7z, excluding generated runtime state
# 2. cargo build --release in launcher/
# 3. pack.ps1 appends bundle.7z to the launcher exe
```

## License

MIT. See [LICENSE](LICENSE). Content from the listed sources retains its
original license.

## Credits

- The six public media sources listed above.
- [portable-linux-in-a-box](https://github.com/aivrar/portable-linux-in-a-box)
  for the launcher/runtime template.
- [hls.js](https://github.com/video-dev/hls.js) for HLS playback.
