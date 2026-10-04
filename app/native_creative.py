"""Enhanced zero-cost creative primitives for RAYONE AI."""
from __future__ import annotations
from pathlib import Path
import hashlib, html, math, re, struct, time, uuid, wave, shutil
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/"data"/"workspace"/"native"; OUT.mkdir(parents=True,exist_ok=True)
def _slug(v):
    s=re.sub(r"[^a-zA-Z0-9]+","-",str(v).strip()).strip("-").lower(); return s[:48] or "rayone"
def _file(kind,ext,prompt):
    return OUT/f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(kind)}-{hashlib.sha256(str(prompt).encode()).hexdigest()[:10]}-{uuid.uuid4().hex[:6]}.{ext}"
def _dims(args,kind):
    ratio=str(args.get("aspect_ratio","16:9"))
    p={"square":(1080,1080),"portrait":(1080,1350),"story":(1080,1920),"reel":(1080,1920),"shorts":(1080,1920),"landscape":(1920,1080),"wide":(1920,1080),"16:9":(1920,1080),"9:16":(1080,1920),"1:1":(1080,1080),"4:5":(1080,1350)}
    if ratio in p:return p[ratio]
    try:a,b=(float(x) for x in ratio.split(":",1));w=1080;return w,max(1,int(w*b/a))
    except Exception:return (1920,1080) if kind in {"image","video"} else (1080,1080)
def _wrap(text,max_chars=42,max_lines=9):
    words=html.escape(str(text)).split();lines=[];line=""
    for word in words:
        if len(line)+len(word)+1>max_chars:
            if line:lines.append(line)
            line=word
        else:line=(line+" "+word).strip()
    if line:lines.append(line)
    return lines[:max_lines] or ["RAYONE AI"]
def _svg(prompt,w,h,title,animated=False):
    lines=_wrap(prompt,50 if w>=1600 else 38);body=[];y=int(h*.32)
    for i,line in enumerate(lines):body.append(f'<text x="{int(w*.08)}" y="{y+i*int(h*.065)}" fill="#eef7f2" font-family="system-ui,sans-serif" font-size="{max(30,int(w*.045))}" font-weight="700">{line}</text>')
    anim=f'<style>@keyframes rayonePulse{{0%,100%{{opacity:.2}}50%{{opacity:.9}}}}</style><circle cx="{w-160}" cy="160" r="70" fill="#8fe0b3" style="animation:rayonePulse 2.4s ease-in-out infinite"/>' if animated else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#07131a"/><stop offset=".55" stop-color="#143b42"/><stop offset="1" stop-color="#05090c"/></linearGradient><radialGradient id="glow"><stop stop-color="#8fe0b3" stop-opacity=".30"/><stop offset="1" stop-color="#8fe0b3" stop-opacity="0"/></radialGradient></defs>
<rect width="100%" height="100%" fill="url(#bg)"/><circle cx="{int(w*.82)}" cy="{int(h*.22)}" r="{int(min(w,h)*.30)}" fill="url(#glow)"/><rect x="{int(w*.035)}" y="{int(h*.035)}" width="{int(w*.93)}" height="{int(h*.93)}" rx="{int(min(w,h)*.025)}" fill="none" stroke="#8fe0b3" stroke-opacity=".28" stroke-width="2"/>
<text x="{int(w*.08)}" y="{int(h*.16)}" fill="#8fe0b3" font-family="system-ui,sans-serif" font-size="{max(22,int(w*.022))}" font-weight="800" letter-spacing="5">{html.escape(title)}</text>{''.join(body)}
<text x="{int(w*.08)}" y="{int(h*.92)}" fill="#91a6ad" font-family="system-ui,sans-serif" font-size="{max(14,int(w*.014))}">RAYONE AI · native zero-cost renderer</text>{anim}</svg>'''
def _wav(path,notes,seconds=.22,rate=44100):
    frames=bytearray()
    for freq in notes:
        n=max(1,int(rate*seconds))
        for i in range(n):
            t=i/rate;env=min(1,i/(rate*.02),(n-i)/(rate*.04));frames.extend(struct.pack("<h",int(9000*env*math.sin(2*math.pi*float(freq)*t))))
    with wave.open(str(path),"wb") as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(frames)
def _result(path,kind,prompt,mime,**extra):
    return {"ok":True,"engine":"rayone-native-enhanced","kind":kind,"prompt":str(prompt),"path":str(path.relative_to(ROOT)),"file":path.name,"mime":mime,"size":path.stat().st_size,"native":True,"quality":"pipeline-ready",**extra}
def generate_image(args):
    p=args.get("prompt",args.get("text","RAYONE AI image"));w,h=_dims(args,"image");path=_file("image","svg",p);path.write_text(_svg(p,w,h,"RAYONE IMAGE"),encoding="utf-8");return _result(path,"image",p,"image/svg+xml",width=w,height=h)
def generate_design(args):
    p=args.get("prompt",args.get("text","RAYONE AI design"));w,h=_dims(args,"design");path=_file("design","svg",p);path.write_text(_svg(p,w,h,"RAYONE DESIGN"),encoding="utf-8");return _result(path,"design",p,"image/svg+xml",width=w,height=h)
def generate_audio(args):
    p=args.get("prompt",args.get("text","RAYONE audio"));path=_file("audio","wav",p);_wav(path,[220,277.18,329.63,440],.28);return _result(path,"audio",p,"audio/wav",sample_rate=44100)
def generate_music(args):
    p=args.get("prompt",args.get("text","RAYONE music"));path=_file("music","wav",p);_wav(path,[261.63,293.66,329.63,392,440,523.25,659.25,523.25,440,392,329.63,293.66],.20);return _result(path,"music",p,"audio/wav",sample_rate=44100)
def generate_voice(args):
    p=args.get("prompt",args.get("text","RAYONE voice"));path=_file("voice","wav",p);_wav(path,[175+(i%7)*27 for i in range(min(max(4,len(str(p).split())*2),40))],.10);return _result(path,"voice",p,"audio/wav",sample_rate=44100,tts_engine="native-carrier")
def generate_video(args):
    p=args.get("prompt",args.get("text","RAYONE video"));w,h=_dims(args,"video");path=_file("video","svg",p);path.write_text(_svg(p,w,h,"RAYONE VIDEO",True),encoding="utf-8");return _result(path,"video",p,"image/svg+xml",width=w,height=h,animation=True,format="animated-svg")
def engine_info():
    return {"engine":"rayone-native-enhanced","local":True,"paid_dependency":False,"ffmpeg_available":bool(shutil.which("ffmpeg")),"capabilities":["image","design","audio","music","voice","video"],"quality_note":"Deterministic local rendering; learned generative models remain optional adapters."}
