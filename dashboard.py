"""The staff admin dashboard — a single self-contained HTML page.

Served at GET /admin (behind HTTP Basic auth). It polls /admin/api/sessions and
lets staff edit a description and re-send it via /admin/api/sessions/{id}/resend.
No external assets, no localStorage — safe to serve as-is.
"""

from __future__ import annotations

from config import Settings

_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alt-Text Dashboard __EVENT_NAME__</title>
<style>
  :root {
    --bg:#0f1420; --panel:#182033; --panel2:#1f293f; --text:#e8edf7;
    --muted:#9aa7bd; --line:#2b3752; --accent:#4f8cff; --accent2:#3a6fd6;
    --ok:#2ec16b; --warn:#f0a92b; --bad:#f0554e; --pending:#7b8dff;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg:#f4f6fb; --panel:#ffffff; --panel2:#f0f3fa; --text:#141a26;
      --muted:#5a677e; --line:#dce3ef; --accent:#2f6bdc; --accent2:#2456bf;
      --ok:#12925a; --warn:#b9791a; --bad:#c9382f; --pending:#4b5cd0;
    }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  header { position:sticky; top:0; z-index:5; background:var(--panel);
    border-bottom:1px solid var(--line); padding:14px 20px;
    display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  header h1 { font-size:17px; margin:0; font-weight:650; }
  .badge-event { color:var(--muted); font-weight:500; }
  .stats { display:flex; gap:10px; flex-wrap:wrap; margin-left:auto; }
  .stat { background:var(--panel2); border:1px solid var(--line); border-radius:9px;
    padding:6px 11px; min-width:78px; text-align:center; }
  .stat .n { font-size:18px; font-weight:700; display:block; }
  .stat .l { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .controls { padding:12px 20px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .controls label { color:var(--muted); font-size:13px; display:flex; align-items:center; gap:6px; }
  .seg { display:inline-flex; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  .seg button { background:transparent; color:var(--text); border:0; padding:7px 12px;
    cursor:pointer; font-size:13px; }
  .seg button[aria-pressed="true"] { background:var(--accent); color:#fff; }
  .wrap { padding:0 20px 60px; }
  .row { background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:14px; margin:12px 0; display:grid;
    grid-template-columns:76px 1fr 260px; gap:16px; align-items:start; }
  @media (max-width:820px){ .row{ grid-template-columns:1fr; } }
  .thumb { width:76px; height:76px; border-radius:9px; object-fit:cover;
    background:var(--panel2); border:1px solid var(--line); display:block; }
  .thumb.placeholder { display:flex; align-items:center; justify-content:center;
    color:var(--muted); font-size:11px; text-align:center; }
  .meta { min-width:0; }
  .meta .top { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px; }
  .pill { font-size:11px; font-weight:650; padding:3px 9px; border-radius:999px;
    text-transform:uppercase; letter-spacing:.03em; border:1px solid transparent; }
  .pill.type { background:var(--panel2); color:var(--muted); border-color:var(--line); }
  .pill.ok { background:color-mix(in srgb,var(--ok) 18%,transparent); color:var(--ok); }
  .pill.warn { background:color-mix(in srgb,var(--warn) 18%,transparent); color:var(--warn); }
  .pill.bad { background:color-mix(in srgb,var(--bad) 18%,transparent); color:var(--bad); }
  .pill.pending { background:color-mix(in srgb,var(--pending) 18%,transparent); color:var(--pending); }
  .sub { color:var(--muted); font-size:12px; }
  textarea { width:100%; background:var(--panel2); color:var(--text);
    border:1px solid var(--line); border-radius:9px; padding:9px 11px; font:inherit;
    resize:vertical; min-height:58px; margin-top:6px; }
  textarea:focus { outline:2px solid var(--accent); border-color:var(--accent); }
  .side { display:flex; flex-direction:column; gap:8px; }
  .phone { font-variant-numeric:tabular-nums; }
  .actions { display:flex; gap:8px; flex-wrap:wrap; }
  button.act { background:var(--accent); color:#fff; border:0; border-radius:9px;
    padding:9px 14px; font-size:14px; font-weight:600; cursor:pointer; }
  button.act:hover { background:var(--accent2); }
  button.act:disabled { opacity:.5; cursor:default; }
  button.ghost { background:transparent; color:var(--accent); border:1px solid var(--line); }
  .note { font-size:12px; color:var(--muted); }
  .empty { text-align:center; color:var(--muted); padding:60px 0; }
  #toast { position:fixed; bottom:22px; left:50%; transform:translateX(-50%);
    background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:11px 16px; box-shadow:0 8px 30px rgba(0,0,0,.3); opacity:0;
    transition:opacity .2s, transform .2s; pointer-events:none; max-width:90vw; }
  #toast.show { opacity:1; transform:translateX(-50%) translateY(-4px); }
  #toast.bad { border-color:var(--bad); }
  #toast.ok { border-color:var(--ok); }
  a { color:var(--accent); }
</style>
</head>
<body>
<header>
  <h1>Alt-Text Dashboard <span class="badge-event">__EVENT_NAME__</span></h1>
  <div class="stats" id="stats"></div>
</header>

<div class="controls">
  <div class="seg" role="group" aria-label="Filter">
    <button id="f-all" aria-pressed="true" onclick="setFilter('all')">All</button>
    <button id="f-attention" aria-pressed="false" onclick="setFilter('attention')">Needs attention</button>
    <button id="f-sent" aria-pressed="false" onclick="setFilter('sent')">Delivered</button>
  </div>
  <label><input type="checkbox" id="auto" checked> Auto-refresh</label>
  <span class="note" id="lastupdate"></span>
</div>

<div class="wrap"><div id="rows"></div></div>
<div id="toast" role="status" aria-live="polite"></div>

<script>
const BASE = location.pathname.replace(/\/+$/, "");  // e.g. "/admin"
let FILTER = "all";
let PAUSE = false;          // paused while a textarea is focused or a send is running
let LAST = [];

function setFilter(f){
  FILTER = f;
  for (const k of ["all","attention","sent"]) {
    document.getElementById("f-"+k).setAttribute("aria-pressed", String(k===f));
  }
  render(LAST);
}

function maskPhone(p){
  if(!p) return "";
  const s = String(p).replace(/\s+/g,"");
  if (s.length < 5) return s;
  return s.slice(0,2) + "••••" + s.slice(-4);
}

function esc(s){ return (s??"").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function needsAttention(r){
  return r.caption_status === "failed"
      || r.delivery_status === "failed"
      || (r.delivery_status === "pending")
      || (r.share_method === "sms" && !r.share_phone);
}

function statusPills(r){
  let out = "";
  const cap = r.caption_status;
  if (cap === "ready") out += '<span class="pill ok">described</span>';
  else if (cap === "failed") out += '<span class="pill bad">describe failed</span>';
  else out += '<span class="pill warn">describing…</span>';

  const d = r.delivery_status;
  if (d === "sent") out += ' <span class="pill ok">text sent'
      + (r.resend_count>0 ? " ×"+(r.resend_count) : "") + '</span>';
  else if (d === "failed") out += ' <span class="pill bad">send failed</span>';
  else if (d === "pending") out += ' <span class="pill pending">awaiting text</span>';
  else if (d === "skipped") out += ' <span class="pill">email share</span>';
  else out += ' <span class="pill">not shared</span>';
  return out;
}

function timeAgo(iso){
  if(!iso) return "";
  const t = new Date(iso).getTime();
  const s = Math.max(0, Math.floor((Date.now()-t)/1000));
  if (s<60) return s+"s ago";
  if (s<3600) return Math.floor(s/60)+"m ago";
  return Math.floor(s/3600)+"h ago";
}

function rowHtml(r){
  const isImg = ["photo","gif","ai"].includes((r.media_type||"").toLowerCase());
  const thumb = (isImg && r.media_url)
    ? '<img class="thumb" alt="captured media thumbnail" src="'+esc(r.media_url)
        +'" onerror="this.replaceWith(Object.assign(document.createElement(\'div\'),'
        +'{className:\'thumb placeholder\',textContent:\''+esc((r.media_type||"media"))+'\'}))">'
    : '<div class="thumb placeholder">'+esc((r.media_type||"media"))+'</div>';

  const phone = r.share_phone
    ? '<span class="phone" title="'+esc(r.share_phone)+'">'+esc(maskPhone(r.share_phone))+'</span>'
    : '<span class="sub">no number on file</span>';

  const err = r.delivery_error || r.caption_error;

  return '<div class="row" data-id="'+esc(r.session_id)+'">'
    + thumb
    + '<div class="meta">'
      + '<div class="top">'
        + '<span class="pill type">'+esc(r.media_type||"?")+'</span>'
        + statusPills(r)
        + '<span class="sub"> · '+timeAgo(r.created_at)+'</span>'
      + '</div>'
      + '<label class="sub" for="ta-'+esc(r.session_id)+'">Description (edit before resending)</label>'
      + '<textarea id="ta-'+esc(r.session_id)+'" '
        + 'onfocus="PAUSE=true" onblur="PAUSE=false">'+esc(r.alt_text||"")+'</textarea>'
      + (err ? '<div class="sub" style="color:var(--bad)">⚠ '+esc(err)+'</div>' : '')
    + '</div>'
    + '<div class="side">'
      + '<div class="sub">To: '+phone+'</div>'
      + '<div class="actions">'
        + '<button class="act" onclick="resend(\''+esc(r.session_id)+'\')">'
        + (r.delivery_status==="sent" ? "Resend correction" : "Send description") + '</button>'
      + '</div>'
      + (r.twilio_message_sid ? '<div class="note">last sid: '+esc(r.twilio_message_sid)+'</div>' : '')
    + '</div>'
  + '</div>';
}

function render(rows){
  LAST = rows;
  const stats = {total:rows.length, ready:0, sent:0, attention:0};
  for (const r of rows){
    if (r.caption_status==="ready") stats.ready++;
    if (r.delivery_status==="sent") stats.sent++;
    if (needsAttention(r)) stats.attention++;
  }
  document.getElementById("stats").innerHTML =
      stat("Captured", stats.total)
    + stat("Described", stats.ready)
    + stat("Delivered", stats.sent)
    + stat("Attention", stats.attention);

  let shown = rows;
  if (FILTER==="attention") shown = rows.filter(needsAttention);
  else if (FILTER==="sent") shown = rows.filter(r=>r.delivery_status==="sent");

  const host = document.getElementById("rows");
  if (!shown.length){ host.innerHTML = '<div class="empty">No sessions yet. '
    + 'Take a photo at the booth and it will appear here.</div>'; return; }
  host.innerHTML = shown.map(rowHtml).join("");
}

function stat(label,n){
  return '<div class="stat"><span class="n">'+n+'</span><span class="l">'+label+'</span></div>';
}

async function refresh(){
  if (PAUSE) return;
  try {
    const res = await fetch(BASE+"/api/sessions?limit=200", {headers:{"Accept":"application/json"}});
    if (!res.ok) throw new Error("HTTP "+res.status);
    const data = await res.json();
    render(data);
    document.getElementById("lastupdate").textContent =
        "updated " + new Date().toLocaleTimeString();
  } catch (e){ toast("Couldn't refresh: "+e.message, "bad"); }
}

async function resend(id){
  const ta = document.getElementById("ta-"+id);
  const text = ta ? ta.value : "";
  const btns = document.querySelectorAll('.row[data-id="'+CSS.escape(id)+'"] button.act');
  btns.forEach(b=>b.disabled=true);
  PAUSE = true;
  try {
    const res = await fetch(BASE+"/api/sessions/"+encodeURIComponent(id)+"/resend", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({alt_text:text})
    });
    const out = await res.json();
    if (res.ok && out.ok){ toast("Text sent ✓", "ok"); }
    else { toast("Failed: " + (out.error || ("HTTP "+res.status)), "bad"); }
  } catch(e){ toast("Failed: "+e.message, "bad"); }
  finally {
    btns.forEach(b=>b.disabled=false);
    PAUSE = false;
    refresh();
  }
}

let toastTimer=null;
function toast(msg, kind){
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "show " + (kind||"");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(()=>{ t.className = t.className.replace("show","").trim(); }, 3200);
}

document.getElementById("auto").addEventListener("change", e=>{
  if (e.target.checked) startAuto(); else stopAuto();
});
let auto=null;
function startAuto(){ stopAuto(); auto=setInterval(refresh, 4000); }
function stopAuto(){ if(auto) clearInterval(auto); auto=null; }

refresh();
startAuto();
</script>
</body>
</html>
"""


def render_dashboard(settings: Settings) -> str:
    event = f"— {settings.event_name}" if settings.event_name else ""
    return _PAGE.replace("__EVENT_NAME__", event)
