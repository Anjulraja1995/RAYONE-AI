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
    """Resolve natural language to a concrete registered executable capability.

    Native routing is deterministic and parameter-aware.  It deliberately
    returns None when the request cannot be mapped safely instead of pretending
    that a generic placeholder executed the task.
    """
    m=str(message or "").strip()
    low=m.lower()

    # Exact high-confidence numeric conversions.
    specs=[
        (r"(-?\d+(?:\.\d+)?)\s*(?:km|kilometers?)\b.*\b(?:miles?)\b","local.conversion.km_miles"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:miles?)\b.*\b(?:km|kilometers?)\b","local.conversion.miles_km"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:kg|kilograms?)\b.*\b(?:lb|pounds?)\b","local.conversion.kg_lb"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:lb|pounds?)\b.*\b(?:kg|kilograms?)\b","local.conversion.lb_kg"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:meters?|m)\b.*\b(?:feet|ft)\b","local.conversion.meters_feet"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:feet|ft)\b.*\b(?:meters?|m)\b","local.conversion.feet_meters"),
    ]
    for pattern,target in specs:
        q=re.search(pattern,low,re.I)
        if q:return target,{"value":float(q.group(1))}
    q=re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°?\s*c|celsius)\b.*\b(?:fahrenheit|°?f)\b",low,re.I)
    if q:return "local.conversion.celsius_fahrenheit",{"value":float(q.group(1))}
    q=re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°?\s*f|fahrenheit)\b.*\b(?:celsius|°?c)\b",low,re.I)
    if q:return "local.conversion.fahrenheit_celsius",{"value":float(q.group(1))}
    q=re.search(r"(-?\d+(?:\.\d+)?)\s*%\s*(?:of|का|की|के)\s*(-?\d+(?:\.\d+)?)",low)
    if q:return "local.math.percent",{"value":float(q.group(2)),"rate":float(q.group(1))}
    q=re.search(r"(?:discount|छूट)\D*(\d+(?:\.\d+)?)\D*(?:%|percent)",low)
    if q:
        nums=re.findall(r"\d+(?:\.\d+)?",low)
        if len(nums)>=2:return "local.finance.discount",{"value":float(nums[0]),"rate":float(nums[1])}

    # Arithmetic, including natural questions without the word "calculate".
    expr=re.search(r"(?i)(?:what is|what's|whats|solve|evaluate|calculate|compute)\s*([0-9\s()+\-*/%.×÷^]+)\s*\??$",m)
    if expr:
        e=expr.group(1).strip().replace("×","*").replace("÷","/").replace("^","**")
        if re.fullmatch(r"[0-9\s()+\-*/%.]+",e):
            return "core.calculator",{"expression":e}
    if re.fullmatch(r"[\s0-9()+\-*/%.×÷^]+",m) and re.search(r"[+*/%×÷^\-]",m):
        e=m.replace("×","*").replace("÷","/").replace("^","**")
        return "core.calculator",{"expression":e}

    # Direct text transforms.
    for words,target in [
        (("uppercase","upper","capitalise","capitalize"),"local.text.upper"),
        (("lowercase","lower"),"local.text.lower"),
        (("title case","titlecase"),"local.text.title"),
        (("slug","url slug"),"local.text.slug"),
        (("trim","strip whitespace"),"local.text.trim"),
        (("reverse text","reverse"),"local.text.reverse"),
        (("count words","word count"),"local.text.words"),
        (("count characters","character count","char count"),"local.text.length"),
    ]:
        if any(re.search(r"\b"+re.escape(w)+r"\b",low) for w in words):
            body=re.sub(r"(?i)^(?:please\s+)?(?:make|convert|do)\s+","",m)
            for w in words: body=re.sub(r"(?i)\b"+re.escape(w)+r"\b","",body)
            body=body.strip(" :,-")
            return target,{"text":body}

    # Encoding, hashing, JSON.
    simple=[
        (r"(?:base64\s+encode|encode\s+base64)","local.encoding.base64_encode"),
        (r"(?:base64\s+decode|decode\s+base64)","local.encoding.base64_decode"),
        (r"(?:sha256|hash\s+sha256)","local.crypto.sha256"),
        (r"(?:sha1|hash\s+sha1)","local.crypto.sha1"),
        (r"(?:sha512|hash\s+sha512)","local.crypto.sha512"),
        (r"(?:md5|hash\s+md5)","local.crypto.md5"),
    ]
    for pat,target in simple:
        if re.search(pat,low,re.I):
            body=re.sub(pat,"",m,flags=re.I).strip(" :,-")
            return target,{"text":body}
    if re.search(r"\b(?:generate|create)?\s*uuid\b",low): return "local.crypto.uuid",{}
    if re.search(r"\b(?:json\s+parse|parse\s+json)\b",low):
        body=re.sub(r"(?i)\b(?:json\s+parse|parse\s+json)\b","",m).strip(" :,-")
        try: return "local.json.parse",{"value":json.loads(body)}
        except Exception: return "local.json.parse",{"value":body}
    if re.search(r"\b(?:json\s+stringify|stringify\s+json)\b",low):
        body=re.sub(r"(?i)\b(?:json\s+stringify|stringify\s+json)\b","",m).strip(" :,-")
        try: value=json.loads(body)
        except Exception: value=body
        return "local.json.stringify",{"value":value}

    # Numeric/statistical operations.
    number_ops=[
        (r"(?:square\s+root|sqrt)\D*(-?\d+(?:\.\d+)?)","local.math.sqrt"),
        (r"(?:factorial)\D*(\d+)","local.math.factorial"),
        (r"(?:is\s+prime|prime\s+number)\D*(\d+)","local.math.is_prime"),
        (r"(?:gcd)\D*(-?\d+)\D+(-?\d+)","local.math.gcd"),
        (r"(?:lcm)\D*(-?\d+)\D+(-?\d+)","local.math.lcm"),
    ]
    for pat,target in number_ops:
        q=re.search(pat,low,re.I)
        if q:
            if target.endswith((".gcd",".lcm")): return target,{"a":int(q.group(1)),"b":int(q.group(2))}
            return target,{"value":float(q.group(1))}
    if re.search(r"\b(?:today|current date|date today|आज की तारीख)\b",low): return "local.datetime.date",{}
    if re.search(r"\b(?:current time|what time is it|time now|अभी समय)\b",low): return "local.datetime.time",{}

    # Common structured/data capabilities.
    structured=[
        (r"\b(?:unique|dedupe|remove duplicates)\b","local.list.unique"),
        (r"\b(?:sort|sort this list)\b","local.list.sort"),
        (r"\b(?:first item|first element)\b","local.list.first"),
        (r"\b(?:last item|last element)\b","local.list.last"),
        (r"\b(?:count items|how many items)\b","local.list.count"),
        (r"\b(?:escape html|html escape)\b","local.html.escape"),
        (r"\b(?:strip html|remove html tags)\b","local.html.strip_tags"),
        (r"\b(?:parse url|parse this url)\b","local.url.parse"),
        (r"\b(?:is https|https url)\b","local.url.is_https"),
    ]
    for pat,target in structured:
        if re.search(pat,low,re.I):
            body=re.sub(pat,"",m,flags=re.I).strip(" :,-")
            if target.startswith("local.list."):
                try: return target,{"items":json.loads(body)}
                except Exception: return target,{"items":[x.strip() for x in body.split(",") if x.strip()]}
            return target,{"value":body,"text":body,"url":body}

    # Finance and geometry.
    q=re.search(r"\b(?:simple interest)\b.*?\b(?:principal|p)\D*(\d+(?:\.\d+)?)\D+.*?\b(?:rate|r)\D*(\d+(?:\.\d+)?)\D+.*?\b(?:time|years?|t)\D*(\d+(?:\.\d+)?)",low,re.I)
    if q:return "local.finance.simple_interest",{"principal":float(q.group(1)),"rate":float(q.group(2)),"time":float(q.group(3))}
    q=re.search(r"\b(?:circle area|area of circle)\b\D*(\d+(?:\.\d+)?)",low,re.I)
    if q:return "local.geometry.circle_area",{"radius":float(q.group(1))}
    q=re.search(r"\b(?:rectangle area|area of rectangle)\b\D*(\d+(?:\.\d+)?)\D+(\d+(?:\.\d+)?)",low,re.I)
    if q:return "local.geometry.rectangle_area",{"length":float(q.group(1)),"width":float(q.group(2))}

    # Fall back to the existing exact aliases.
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
