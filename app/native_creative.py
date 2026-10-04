"""RAYONE native creative engine.

Zero-cost, dependency-light creative primitives. These are real local generators,
not provider placeholders. They create inspectable artifacts directly under the
RAYONE workspace and return their metadata for the shared tool executor.
"""
from pathlib import Path
import hashlib
import html
import math
import re
import struct
import time
import uuid
import wave

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "workspace" / "native"
OUT.mkdir(parents=True, exist_ok=True)

def _slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip()).strip("-").lower()
    return s[:48] or "rayone"

def _file(kind: str, ext: str, prompt: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()[:8]
    return OUT / f"{stamp}-{_slug(kind)}-{digest}-{uuid.uuid4().hex[:6]}.{ext}"

def _svg_text(prompt: str, width: int = 1280, height: int = 720, title: str = "RAYONE AI") -> str:
    safe = html.escape(str(prompt))
    # Keep long prompts readable without requiring a font/rendering dependency.
    words = safe.split()
    lines, line = [], ""
    for word in words:
        if len(line) + len(word) + 1 > 46:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        lines.append(line)
    lines = lines[:7] or ["RAYONE native creative output"]
    tspans = "".join(f'<tspan x="80" dy="{58 if i else 0}">{x}</tspan>' for i, x in enumerate(lines))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#08151d"/><stop offset=".55" stop-color="#153847"/><stop offset="1" stop-color="#071016"/></linearGradient></defs>
<rect width="100%" height="100%" fill="url(#g)"/>
<circle cx="{width-140}" cy="120" r="190" fill="#8fe0b3" opacity=".10"/>
<circle cx="{width-240}" cy="{height-40}" r="260" fill="#7cc9ff" opacity=".07"/>
<rect x="42" y="42" width="{width-84}" height="{height-84}" rx="28" fill="none" stroke="#8fe0b3" opacity=".32"/>
<text x="80" y="110" fill="#8fe0b3" font-family="system-ui,sans-serif" font-size="30" font-weight="800" letter-spacing="4">{html.escape(title)}</text>
<text x="80" y="205" fill="#e8f1f5" font-family="system-ui,sans-serif" font-size="48" font-weight="700">{tspans}</text>
<text x="80" y="{height-70}" fill="#8ea3ad" font-family="system-ui,sans-serif" font-size="18">Native local creative engine · no paid provider</text>
</svg>'''

def _write_wav(path: Path, notes, seconds_per_note=0.24, sample_rate=22050):
    frames = bytearray()
    amp = 11000
    for freq in notes:
        n = max(1, int(sample_rate * seconds_per_note))
        for i in range(n):
            t = i / sample_rate
            envelope = min(1.0, i / max(1, sample_rate * .02), (n-i) / max(1, sample_rate * .04))
            sample = int(amp * envelope * math.sin(2 * math.pi * float(freq) * t))
            frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(frames)

def _result(path: Path, kind: str, prompt: str, mime: str):
    return {"ok": True, "engine": "rayone-native", "kind": kind, "prompt": str(prompt),
            "path": str(path.relative_to(ROOT)), "file": str(path.name), "mime": mime,
            "size": path.stat().st_size, "native": True}

def generate_image(args):
    prompt = args.get("prompt", args.get("text", "RAYONE AI native image"))
    path = _file("image", "svg", prompt)
    path.write_text(_svg_text(prompt), encoding="utf-8")
    return _result(path, "image", prompt, "image/svg+xml")

def generate_design(args):
    prompt = args.get("prompt", args.get("text", "RAYONE AI native design"))
    path = _file("design", "svg", prompt)
    path.write_text(_svg_text(prompt, 1440, 900, "RAYONE DESIGN"), encoding="utf-8")
    return _result(path, "design", prompt, "image/svg+xml")

def generate_audio(args):
    prompt = args.get("prompt", args.get("text", "RAYONE audio"))
    path = _file("audio", "wav", prompt)
    _write_wav(path, [220, 277.18, 329.63, 440], .28)
    return _result(path, "audio", prompt, "audio/wav")

def generate_music(args):
    prompt = args.get("prompt", args.get("text", "RAYONE music"))
    path = _file("music", "wav", prompt)
    _write_wav(path, [261.63, 329.63, 392.0, 523.25, 392.0, 329.63, 293.66, 261.63], .22)
    return _result(path, "music", prompt, "audio/wav")

def generate_voice(args):
    prompt = args.get("prompt", args.get("text", "RAYONE voice"))
    # Native baseline voice: deterministic speech-like carrier. It is intentionally
    # exposed as a real audio artifact, while higher-quality phoneme engines can be
    # added later without changing the tool contract.
    path = _file("voice", "wav", prompt)
    words = max(1, len(str(prompt).split()))
    notes = [180 + (i % 5) * 35 for i in range(min(words * 2, 32))]
    _write_wav(path, notes, .11)
    return _result(path, "voice", prompt, "audio/wav")

def generate_video(args):
    prompt = args.get("prompt", args.get("text", "RAYONE native animation"))
    path = _file("video", "svg", prompt)
    svg = _svg_text(prompt, 1280, 720, "RAYONE VIDEO")
    svg = svg.replace("</svg>", '<style>@keyframes rayonePulse{0%,100%{opacity:.25}50%{opacity:.85}}</style><circle cx="1080" cy="170" r="70" fill="#8fe0b3" style="animation:rayonePulse 2s ease-in-out infinite"/></svg>')
    path.write_text(svg, encoding="utf-8")
    result = _result(path, "video", prompt, "image/svg+xml")
    result["animation"] = True
    result["format"] = "animated-svg"
    return result
