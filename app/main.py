async def connectivity(_:str=Depends(auth)):
    results=[]
    for p in rows("select * from providers where enabled=1"):
        ok=False;err=""
        try:
            async with httpx.AsyncClient(timeout=8) as client:r=await client.get(p["base_url"]);ok=r.status_code<500
        except Exception as e:err=str(e)
        results.append({"id":p["id"],"name":p["name"],"ok":ok,"error":err})
    return {"database":True,"providers":results,"tools":len(rows("select id from tools where enabled=1")),"agents":len(rows("select id from agents where enabled=1")),"workflows":len(rows("select id from workflows where enabled=1"))}

HTML=(ROOT / 'app' / 'dashboard.html').read_text(encoding='utf-8')


@app.get("/",response_class=HTMLResponse)
def ui():return HTML

@app.on_event("startup")
async def startup():asyncio.create_task(worker_loop())


# RAYONE v2 universal control plane and VORQYON execution layer
from .advanced import router as advanced_router, scheduler_loop
from .native_engines import router as native_engines_router
app.include_router(advanced_router)
app.include_router(native_engines_router)

@app.on_event("startup")
async def advanced_startup():
    asyncio.create_task(scheduler_loop())