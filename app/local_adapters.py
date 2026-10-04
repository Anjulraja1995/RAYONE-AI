"""Optional local-first adapters for browser-like fetch, OCR and media inspection.
No paid service is required; capabilities degrade explicitly when a native runtime is absent.
"""
import io, json, mimetypes, os, re, subprocess, tempfile, urllib.parse, urllib.request, html as html_lib
from pathlib import Path

def browser_fetch(url, max_bytes=200000):
    if not re.match(r"^https?://", url, re.I): raise ValueError("Only http/https URLs are supported")
    req=urllib.request.Request(url,headers={"User-Agent":"RAYONE-AI/2.0 local-browser"})
    with urllib.request.urlopen(req,timeout=20) as r:
        data=r.read(max_bytes+1); content_type=r.headers.get("Content-Type","")
        return {"url":url,"status":getattr(r,"status",200),"content_type":content_type,"truncated":len(data)>max_bytes,"content":data[:max_bytes].decode("utf-8","replace")}

def extract_text(html):
    html=re.sub(r"<script[\\s\\S]*?</script>|<style[\\s\\S]*?</style>"," ",html,flags=re.I)
    html=re.sub(r"<[^>]+>"," ",html)
    return re.sub(r"\\s+"," ",html).strip()

def ocr_image(path):
    try:
        import pytesseract
        from PIL import Image
        return {"engine":"pytesseract","text":pytesseract.image_to_string(Image.open(path))}
    except Exception as e:
        binary="tesseract"
        try:
            p=subprocess.run([binary,str(path),"stdout","--dpi","300"],capture_output=True,text=True,timeout=30)
            if p.returncode==0:return {"engine":"tesseract-cli","text":p.stdout}
        except Exception: pass
        return {"engine":None,"available":False,"text":"","reason":str(e)}

def media_probe(path):
    p=Path(path); mime=mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    result={"name":p.name,"size":p.stat().st_size,"mime":mime,"kind":mime.split("/",1)[0]}
    if mime.startswith("image/"):
        try:
            from PIL import Image
            im=Image.open(p); result.update({"width":im.width,"height":im.height,"format":im.format,"mode":im.mode})
        except Exception as e: result["image_error"]=str(e)
    elif mime.startswith("audio/"):
        try:
            import wave
            with wave.open(str(p),"rb") as w: result.update({"channels":w.getnchannels(),"sample_rate":w.getframerate(),"frames":w.getnframes(),"duration":w.getnframes()/w.getframerate()})
        except Exception: pass
    return result


def browser_fetch_text(url, max_bytes=200000):
    """Native research primitive: fetch a page and return normalized readable text."""
    raw=browser_fetch(url,max_bytes=max_bytes)
    raw["text"]=extract_text(raw.get("content",""))
    return raw


def translate_local(text, target, source="auto"):
    """Deterministic zero-cost language helper for common UI/assistant phrases."""
    value=str(text or "")
    target=(target or "en").lower()
    source=(source or "auto").lower()
    # Keep arbitrary text intact when no safe local translation dictionary exists.
    dictionaries={
        "hi":{"hello":"नमस्ते","hi":"नमस्ते","thank you":"धन्यवाद","thanks":"धन्यवाद","yes":"हाँ","no":"नहीं","please":"कृपया","good morning":"सुप्रभात","goodbye":"अलविदा"},
        "en":{"नमस्ते":"hello","धन्यवाद":"thank you","हाँ":"yes","नहीं":"no","कृपया":"please","सुप्रभात":"good morning","अलविदा":"goodbye"}
    }
    if target in dictionaries:
        return {"text":dictionaries[target].get(value.lower(),value),"source":source,"target":target,"native":True}
    return {"text":value,"source":source,"target":target,"native":True,"note":"passthrough for unsupported local language pair"}
