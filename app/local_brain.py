"""RAYONE local-first intent understanding and planning brain.

This module is deliberately dependency-light. It gives RAYONE a deterministic
execution planner even when no external model is configured. Configured AI
providers can still answer open-ended language through the provider adapter.
"""
from __future__ import annotations
import re
import json
from typing import Any

def _has(text, *terms):
    m=str(text or "").lower()
    return any(t in m for t in terms)

def language(text):
    return "hi" if re.search(r"[\u0900-\u097F]", str(text or "")) else "en"

def classify(message: str, conversation_context: str = "") -> str:
    m=(str(message or "")+" "+str(conversation_context or "")).strip().lower()
    if not m: return "chat"
    if _has(m,"image","photo","picture","poster","thumbnail","design","graphic","इमेज","फोटो","पोस्टर","डिजाइन"):
        return "media"
    if _has(m,"video","reel","shorts","animation","वीडियो","रील"):
        return "media"
    if _has(m,"music","song","lyrics","audio","voice","tts","speech","गाना","सॉन्ग","म्यूजिक","आवाज़"):
        return "media"
    if _has(m,"search","research","latest","news","web","internet","find online","खोज","रिसर्च","लेटेस्ट","न्यूज़","इंटरनेट"):
        return "research"
    if _has(m,"file","document","pdf","docx","xlsx","csv","upload","फाइल","डॉक्यूमेंट","पीडीएफ"):
        return "knowledge"
    if _has(m,"github","gitlab","repository","repo","commit","pull request","issue"):
        return "devops"
    if _has(m,"schedule","remind","reminder","every day","every hour","cron","याद दिला","रिमाइंड","शेड्यूल"):
        return "automation"
    if _has(m,"workflow","automate this","steps","वर्कफ्लो","ऑटोमेट"):
        return "workflow"
    if _has(m,"calculate","calculator","compute","math","sum","add ","subtract","multiply","divide",
            "गणना","कैलकुलेट","जोड़","घटाव","गुणा","भाग"):
        return "tool"
    if _has(m,"plan","planning","roadmap","steps","checklist","योजना","प्लान","रोडमैप","स्टेप्स"):
        return "planning"
    return "chat"

def is_capability_question(message: str) -> bool:
    m=str(message or "").strip().lower()
    if not m: return False
    question = "?" in m or any(x in m for x in ("can you","could you","are you able","do you support","क्या तुम","क्या आप","कर सकते हो","कर सकती हो","बना सकते","बना सकती","bana sakte ho","bana sakti ho","kar sakte ho","kar sakti ho","can bana","can make"))
    action = any(x in m for x in ("make","create","generate","build","bana","banao","बन","तैयार"))
    return bool(question and action)

def is_status_followup(message: str) -> bool:
    m=str(message or "").strip().lower()
    return m in {"ky hua","kya hua","status","update","what happened","what's happening","क्या हुआ","स्टेटस","अपडेट","हुआ क्या"}

def is_execute_followup(message: str) -> bool:
    m=str(message or "").strip().lower()
    return bool(re.match(r"^(banao|banाओ|create it|make it|do it|go ahead|ok|okay|complete|complete it|yes|haan|ha|करो|बनाओ|बना दो|ठीक है|कम्प्लीट करो|कर दो|हाँ|हां)\b",m))

def split_compound_tasks(message: str) -> list[str]:
    """Split only explicit task sequencing; preserve ordinary creative prose."""
    msg=str(message or "").strip()
    if not msg: return []
    parts=re.split(r"\s*(?:;|\n+|\bthen\b|\band then\b|\bafter that\b|\bफिर\b|\bऔर फिर\b)\s*",msg,flags=re.I)
    return [p.strip(" .") for p in parts if p.strip(" .")]


def resolve_followup(message: str, context: list[dict] | None = None, pending: dict | None = None) -> dict[str, str]:
    msg=str(message or "").strip()
    if is_status_followup(msg):
        return {"intent":"status","kind":"status","message":msg}
    if pending and is_execute_followup(msg):
        return {"intent":pending.get("intent","chat"),"kind":pending.get("kind",pending.get("intent","chat")),
                "message":pending.get("message",""),"execute_pending":"true"}
    recent=" ".join(str(x.get("content","")) for x in (context or [])[-8:])
    if recent and is_execute_followup(msg):
        if _has(recent,"image","photo","nature","इमेज","फोटो","चित्र"):
            return {"intent":"media","kind":"image","message":"Create the requested image described in the previous conversation: "+recent[-2500:]}
        if _has(recent,"video","reel","वीडियो","रील"):
            return {"intent":"media","kind":"video","message":"Create the requested video described in the previous conversation: "+recent[-2500:]}
        if _has(recent,"music","song","गाना","म्यूजिक"):
            return {"intent":"media","kind":"music","message":"Create the requested music described in the previous conversation: "+recent[-2500:]}
    return {"intent":classify(msg),"kind":classify(msg),"message":msg}


def infer_local_tool(message: str):
    """Map common natural requests to a real registered local capability."""
    m=str(message or "").strip()
    low=m.lower()
    if re.search(r"\b\d+(?:\.\d+)?\s*(km|kilometers?)\b.*\b(miles?)\b",low):
        n=float(re.search(r"\b(\d+(?:\.\d+)?)\s*(?:km|kilometers?)\b",low).group(1))
        return "local.conversion.km_miles",{"value":n}
    if re.search(r"\b\d+(?:\.\d+)?\s*(miles?)\b.*\b(km|kilometers?)\b",low):
        n=float(re.search(r"\b(\d+(?:\.\d+)?)\s*(?:miles?)\b",low).group(1))
        return "local.conversion.miles_km",{"value":n}
    m_pct=re.search(r"\b(\d+(?:\.\d+)?)\s*%\s*(?:of|का|की|के)\s*(\d+(?:\.\d+)?)",low)
    if m_pct:
        return "local.math.percent",{"value":float(m_pct.group(2)),"rate":float(m_pct.group(1))}
    m_disc=re.search(r"(?:discount|छूट)\D*(\d+(?:\.\d+)?)\D*(?:%|percent)",low)
    if m_disc:
        nums=re.findall(r"\d+(?:\.\d+)?",low)
        if len(nums)>=2:return "local.finance.discount",{"value":float(nums[0]),"rate":float(nums[1])}
    m_temp=re.search(r"\b(-?\d+(?:\.\d+)?)\s*(?:°?\s*c|celsius)\b.*\b(?:f|fahrenheit)\b",low)
    if m_temp:return "local.conversion.celsius_fahrenheit",{"value":float(m_temp.group(1))}
    if low.startswith(("uppercase ","upper ","capitalize ")):
        return "local.text.upper",{"text":m.split(" ",1)[1] if " " in m else ""}
    if low.startswith(("lowercase ","lower ")):
        return "local.text.lower",{"text":m.split(" ",1)[1] if " " in m else ""}
    if low.startswith(("slug ","make a slug ","create a slug ")):
        return "local.text.slug",{"text":re.sub(r"^(make a slug|create a slug|slug)\s*","",m,flags=re.I)}
    if low.startswith(("translate ","अनुवाद ","translate this ")):
        body=re.sub(r"^(translate this|translate|अनुवाद)\s*","",m,flags=re.I).strip()
        target="hi" if any(x in low for x in ("hindi","हिंदी","to hindi","में हिंदी")) else "en"
        return "local.translation.local",{"text":body,"source":"en","target":target}
    if low.startswith(("sha256 ","hash sha256 ")):
        body=re.sub(r"^(hash sha256|sha256)\s*","",m,flags=re.I)
        return "local.crypto.sha256",{"text":body}
    expr_match=re.search(r"(?i)(?:what is|whats|solve|evaluate|calculate|compute)\s*([0-9\s()+\-*/%.×÷^]+)\s*\??$",m)
    if expr_match:
        expr=expr_match.group(1).strip().replace("×","*").replace("÷","/").replace("^","**")
        if re.fullmatch(r"[0-9\s()+\-*/%.]+",expr):
            return "core.calculator",{"expression":expr}
    # Broader natural-language routing for the already executable local pack.
    patterns=[
        (r"\b(?:uppercase|upper|capitalise|capitalize)\b", "local.text.upper", "text"),
        (r"\b(?:lowercase|lower)\b", "local.text.lower", "text"),
        (r"\b(?:slug|url slug)\b", "local.text.slug", "text"),
        (r"\b(?:base64 encode|encode base64)\b", "local.encoding.base64_encode", "text"),
        (r"\b(?:base64 decode|decode base64)\b", "local.encoding.base64_decode", "text"),
        (r"\b(?:json parse|parse json)\b", "local.json.parse", "value"),
        (r"\b(?:json stringify|stringify json|json string)\b", "local.json.stringify", "value"),
        (r"\b(?:sha1|hash sha1)\b", "local.crypto.sha1", "text"),
        (r"\b(?:sha512|hash sha512)\b", "local.crypto.sha512", "text"),
        (r"\b(?:uuid|generate uuid)\b", "local.crypto.uuid", "none"),
        (r"\b(?:is prime|prime number)\b", "local.math.is_prime", "number"),
        (r"\b(?:factorial)\b", "local.math.factorial", "number"),
        (r"\b(?:square root|sqrt)\b", "local.math.sqrt", "number"),
        (r"\b(?:celsius|centigrade)\\b.*\\b(?:fahrenheit|f)\b", "local.conversion.celsius_fahrenheit", "number"),
        (r"\b(?:fahrenheit|f)\\b.*\\b(?:celsius|centigrade|c)\b", "local.conversion.fahrenheit_celsius", "number"),
        (r"\b(?:kg|kilogram|kilograms)\\b.*\\b(?:lb|pound|pounds)\b", "local.conversion.kg_lb", "number"),
        (r"\b(?:lb|pound|pounds)\\b.*\\b(?:kg|kilogram|kilograms)\b", "local.conversion.lb_kg", "number"),
        (r"\b(?:today|current date|date today|आज की तारीख)\b", "core.datetime", "none"),
    ]
    for pattern,target,kind in patterns:
        if re.search(pattern,low,re.I):
            nums=re.findall(r"-?\d+(?:\\.\\d+)?",low)
            if kind=="text":
                body=re.sub(pattern,"",m,flags=re.I).strip(" :,-")
                return target,{"text":body}
            if kind=="value":
                body=re.sub(pattern,"",m,flags=re.I).strip(" :,-")
                try: value=json.loads(body)
                except Exception: value=body
                return target,{"value":value}
            if kind=="number" and nums:
                return target,{"value":float(nums[0])}
            return target,{}
    return None

def calculator_expression(message: str) -> str:
    m=str(message or "").strip()
    m=re.sub(r"^(please\s+)?(calculate|compute|calculator|math)\s*[:\-]?\s*","",m,flags=re.I)
    m=m.replace("×","*").replace("÷","/").replace("−","-")
    m=re.sub(r"(?i)\bwhat is\b|\bwhat's\b|\bcalculate\b","",m).strip(" ?")
    return m

def build_plan(message: str, intent: str|None=None) -> dict[str,Any]:
    msg=str(message or "").strip()
    intent=intent or classify(msg)
    steps=[]
    if intent=="tool":
        steps=[{"step":1,"action":"Understand calculation request"},{"step":2,"action":"Select local calculator"},{"step":3,"action":"Execute safely"},{"step":4,"action":"Verify numeric result"},{"step":5,"action":"Return result"}]
    elif intent=="research":
        steps=[{"step":1,"action":"Understand research query"},{"step":2,"action":"Search available sources"},{"step":3,"action":"Extract relevant content"},{"step":4,"action":"Verify source responses"},{"step":5,"action":"Present findings with sources"}]
    elif intent=="media":
        steps=[{"step":1,"action":"Understand creative brief"},{"step":2,"action":"Select native media engine"},{"step":3,"action":"Generate artifact"},{"step":4,"action":"Verify artifact"},{"step":5,"action":"Return media result"}]
    elif intent=="knowledge":
        steps=[{"step":1,"action":"Locate supplied workspace file"},{"step":2,"action":"Extract supported content"},{"step":3,"action":"Process requested information"},{"step":4,"action":"Verify extraction"},{"step":5,"action":"Return document result"}]
    elif intent=="automation":
        steps=[{"step":1,"action":"Understand schedule"},{"step":2,"action":"Normalize interval or schedule"},{"step":3,"action":"Persist automation"},{"step":4,"action":"Verify schedule"},{"step":5,"action":"Return automation status"}]
    elif intent=="planning":
        steps=[{"step":1,"action":"Understand goal"},{"step":2,"action":"Break goal into milestones"},{"step":3,"action":"Order dependencies"},{"step":4,"action":"Define next actions"},{"step":5,"action":"Return executable plan"}]
    elif intent=="devops":
        steps=[{"step":1,"action":"Understand repository task"},{"step":2,"action":"Locate relevant workspace/repository context"},{"step":3,"action":"Prepare requested operation"},{"step":4,"action":"Verify result"},{"step":5,"action":"Return status"}]
    else:
        steps=[{"step":1,"action":"Understand request"},{"step":2,"action":"Select the best available capability"},{"step":3,"action":"Execute or answer"},{"step":4,"action":"Verify response"},{"step":5,"action":"Return result"}]
    return {"intent":intent,"steps":steps,"execution_policy":"native-first, provider-optional","verified":True}

def local_response(message: str, intent: str) -> str:
    msg=str(message or "").strip()
    hi=language(msg)=="hi"
    low=msg.lower()
    if _has(low,"hello","hi","hey","नमस्ते","हेलो"):
        return "नमस्ते! मैं RAYONE हूँ। आप मुझसे calculation, planning, research, files, media creation, automation और दूसरे उपलब्ध tasks सीधे कर सकते हैं।"
    if _has(low,"who are you","what are you","तुम कौन","आप कौन"):
        return "मैं RAYONE AI हूँ — local-first universal assistant. मैं request को समझकर capability चुनता हूँ, execute करता हूँ, verify करता हूँ और result लौटाता हूँ।"
    if is_capability_question(msg):
        if hi:
            return "हाँ, कर सकता हूँ। जब आप कहेंगे, मैं इसी request को actual execute करूँगा और result दूँगा।"
        return "Yes, I can do that. When you tell me to proceed, I will execute this request and return the actual result."
    if _has(low,"what can you do","capabilities","क्या कर सकते","क्या क्या कर"):
        return "मैं research, calculations, planning, files/documents, code/repository tasks, automation, image/design/video/audio/music/voice generation और registered tools चला सकता हूँ। External AI providers configured हों तो open-ended AI conversation भी उन्हीं के जरिए चलती है।"
    if intent=="planning":
        goal=re.sub(r"(?i)^(make|create|give|build|prepare)\s+(a\s+)?(plan|planning|roadmap|checklist)\s*(for|of|to)?\s*","",msg).strip()
        goal=goal or msg
        return "RAYONE Plan\n\nGoal: "+goal+"\n\n1. Define the target and success criteria.\n2. Break it into concrete milestones.\n3. Order dependencies and priorities.\n4. Execute the first actionable step.\n5. Verify each result and adjust the next step."
    if hi:
        return "मैंने आपका अनुरोध समझ लिया है। अभी उपलब्ध local-first execution path से इसे process कर रहा हूँ। अगर task किसी configured AI model की जरूरत रखता है, तो RAYONE उपलब्ध provider को automatically use करेगा।"
    return "I understood the request. RAYONE will use the best available local capability first, then configured providers when the task needs an external AI model."
