"""RAYONE local-first intent understanding and planning brain.

This module is deliberately dependency-light. It gives RAYONE a deterministic
execution planner even when no external model is configured. Configured AI
providers can still answer open-ended language through the provider adapter.
"""
from __future__ import annotations
import re
from typing import Any

def _has(text, *terms):
    m=str(text or "").lower()
    return any(t in m for t in terms)

def language(text):
    return "hi" if re.search(r"[\u0900-\u097F]", str(text or "")) else "en"

def classify(message: str) -> str:
    m=str(message or "").strip().lower()
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
    if _has(low,"what can you do","capabilities","क्या कर सकते","क्या क्या कर"):
        return "मैं research, calculations, planning, files/documents, code/repository tasks, automation, image/design/video/audio/music/voice generation और registered tools चला सकता हूँ। External AI providers configured हों तो open-ended AI conversation भी उन्हीं के जरिए चलती है।"
    if intent=="planning":
        goal=re.sub(r"(?i)^(make|create|give|build|prepare)\s+(a\s+)?(plan|planning|roadmap|checklist)\s*(for|of|to)?\s*","",msg).strip()
        goal=goal or msg
        return "RAYONE Plan\n\nGoal: "+goal+"\n\n1. Define the target and success criteria.\n2. Break it into concrete milestones.\n3. Order dependencies and priorities.\n4. Execute the first actionable step.\n5. Verify each result and adjust the next step."
    if hi:
        return "मैंने आपका अनुरोध समझ लिया है। अभी उपलब्ध local-first execution path से इसे process कर रहा हूँ। अगर task किसी configured AI model की जरूरत रखता है, तो RAYONE उपलब्ध provider को automatically use करेगा।"
    return "I understood the request. RAYONE will use the best available local capability first, then configured providers when the task needs an external AI model."
