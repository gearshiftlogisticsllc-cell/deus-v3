"""
app/main.py — DEUS 3.0 FastAPI Application
============================================
Entry point for Railway cloud deployment.
Run with: uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

import os
import sys

# Ensure project root is on sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Load .env from project root
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_project_root, ".env"))
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(
    title="DEUS 3.0 API",
    description="Digital Entity Unification System — Cloud Backend",
    version="3.0.0",
)

# CORS — allow GUI and any client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(DASHBOARD_HTML)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DEUS 3.0 — Dashboard</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#0D0D0D;color:#E0E0E0;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh;display:flex;flex-direction:column}
  a{color:#00D4FF;text-decoration:none}a:hover{text-decoration:underline}
  .topbar{background:#111;border-bottom:1px solid #00D4FF33;padding:16px 32px;display:flex;align-items:center;gap:16px}
  .topbar h1{font-size:20px;font-weight:700;color:#00D4FF;letter-spacing:2px}
  .topbar .badge{background:#00D4FF;color:#0D0D0D;font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px}
  .topbar .right{margin-left:auto;display:flex;gap:16px;align-items:center;font-size:13px;color:#888}
  .topbar .dot{width:8px;height:8px;border-radius:50%;background:#00FF88;display:inline-block;animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;padding:32px;flex:1}
  .card{background:#141414;border:1px solid #222;border-radius:10px;padding:24px;transition:border-color .2s}
  .card:hover{border-color:#00D4FF66}
  .card h2{font-size:15px;color:#00D4FF;margin-bottom:12px;display:flex;align-items:center;gap:8px}
  .card h2 .icon{font-size:18px}
  .stat{font-size:32px;font-weight:700;color:#fff;margin:4px 0}
  .stat.ok{color:#00FF88}.stat.warn{color:#FFB800}.stat.err{color:#FF4444}
  .label{font-size:12px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-top:8px}
  .bar{height:6px;background:#222;border-radius:3px;margin-top:8px;overflow:hidden}
  .bar .fill{height:100%;background:#00D4FF;border-radius:3px;transition:width .6s}
  .list{list-style:none;margin-top:12px}
  .list li{padding:8px 0;border-bottom:1px solid #1a1a1a;display:flex;justify-content:space-between;font-size:13px}
  .list li:last-child{border-bottom:none}
  .pill{font-size:11px;padding:2px 8px;border-radius:4px;font-weight:600}
  .pill.ok{background:#00FF8822;color:#00FF88}.pill.err{background:#FF444422;color:#FF4444}
  .pill.off{background:#66666622;color:#666}
  .footer{text-align:center;padding:16px;font-size:11px;color:#444;border-top:1px solid #1a1a1a}
  .actions{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
  .btn{background:#00D4FF;color:#0D0D0D;border:none;padding:8px 16px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;transition:opacity .2s}
  .btn:hover{opacity:.85;text-decoration:none}
</style>
</head>
<body>
<div class="topbar">
  <h1>DEUS 3.0</h1>
  <span class="badge">LIVE</span>
  <div class="right">
    <span><span class="dot"></span> Operational</span>
    <span>v3.0.0</span>
  </div>
</div>

<div class="grid" id="grid">
  <div class="card" id="status-card">
    <h2><span class="icon">&#9889;</span> System Status</h2>
    <div class="stat ok" id="status-text">Loading...</div>
    <div class="label">All services connected</div>
  </div>
  <div class="card">
    <h2><span class="icon">&#129302;</span> Agents</h2>
    <div class="stat" id="agents-stat">--</div>
    <div class="label">Healthy / Total</div>
    <div class="bar"><div class="fill" id="agents-bar" style="width:0%"></div></div>
  </div>
  <div class="card">
    <h2><span class="icon">&#9881;</span> Services</h2>
    <ul class="list" id="services-list">
      <li>Groq <span class="pill off" id="svc-groq">--</span></li>
      <li>Gemini <span class="pill off" id="svc-gemini">--</span></li>
      <li>Calendly <span class="pill off" id="svc-calendly">--</span></li>
      <li>SMTP <span class="pill off" id="svc-smtp">--</span></li>
    </ul>
  </div>
  <div class="card">
    <h2><span class="icon">&#128640;</span> Quick Start</h2>
    <p style="font-size:13px;color:#888;margin-bottom:12px">Access the API endpoints or run agents remotely.</p>
    <div class="actions">
      <a class="btn" href="/docs">API Docs</a>
      <a class="btn" href="/health">Health Check</a>
      <a class="btn" href="/api/agents">List Agents</a>
      <a class="btn" href="/api/pipelines">Pipelines</a>
    </div>
  </div>
</div>

<div class="footer">DEUS 3.0 &mdash; Digital Entity Unification System &mdash; GrowthDesk VA</div>

<script>
fetch('/health').then(r=>r.json()).then(d=>{
  document.getElementById('status-text').textContent = d.status === 'ok' ? 'All Systems Online' : 'Degraded';
  document.getElementById('status-text').className = 'stat ' + (d.status==='ok'?'ok':'err');
  document.getElementById('agents-stat').textContent = d.agents_healthy + ' / ' + d.agents_total;
  document.getElementById('agents-stat').className = 'stat ' + (d.agents_healthy===d.agents_total?'ok':'warn');
  document.getElementById('agents-bar').style.width = (d.agents_healthy/d.agents_total*100)+'%';
  set('svc-groq', d.groq); set('svc-gemini', d.gemini);
  set('svc-calendly', d.calendly); set('svc-smtp', d.smtp);
}).catch(()=>{
  document.getElementById('status-text').textContent = 'Connection Error';
  document.getElementById('status-text').className = 'stat err';
});
function set(id, ok){
  var el=document.getElementById(id);
  el.textContent=ok?'Connected':'Not Set';
  el.className='pill '+(ok?'ok':'off');
}
</script>
</body>
</html>"""
