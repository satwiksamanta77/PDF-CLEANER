#!/usr/bin/env python3
"""Local web UI: pick a PDF, choose what to remove, download V2."""
import os
import sys
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clean_for_canva import clean_pdf, v2_name

BASE = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(BASE, "PDFS TO CLEAN")
OUT_DIR = os.path.join(BASE, "OUTPUT")
os.makedirs(OUT_DIR, exist_ok=True)

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canva PDF Cleaner</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:Segoe UI,system-ui,sans-serif;background:#0f1117;color:#e8eaf0;min-height:100vh;padding:32px 16px}
  .wrap{max-width:720px;margin:0 auto}
  h1{font-size:1.5rem;margin-bottom:6px;color:#7dd3fc}
  .sub{color:#8b93a7;font-size:.9rem;margin-bottom:28px}
  .card{background:#181c27;border:1px solid #2a3040;border-radius:12px;padding:20px;margin-bottom:16px}
  .card h2{font-size:1rem;margin-bottom:12px;color:#c9d1e0}
  select{width:100%;padding:10px 12px;background:#0f1117;color:#e8eaf0;border:1px solid #3a4258;border-radius:8px;font-size:.95rem}
  .opts{display:flex;flex-direction:column;gap:10px}
  .opt{display:flex;align-items:flex-start;gap:10px;padding:12px;background:#0f1117;border:1px solid #2a3040;border-radius:8px;cursor:pointer}
  .opt:hover{border-color:#7dd3fc55}
  .opt input{margin-top:3px;accent-color:#7dd3fc;width:16px;height:16px}
  .opt .lbl{font-size:.95rem}
  .opt .desc{font-size:.8rem;color:#8b93a7;margin-top:2px}
  .preset{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
  .preset button{padding:8px 14px;background:#232938;color:#c9d1e0;border:1px solid #3a4258;border-radius:8px;cursor:pointer;font-size:.85rem}
  .preset button:hover{border-color:#7dd3fc}
  .preset button.active{background:#0c4a6e;border-color:#7dd3fc;color:#e0f2fe}
  .go{width:100%;padding:14px;background:#0284c7;color:#fff;border:none;border-radius:10px;font-size:1.05rem;font-weight:600;cursor:pointer;margin-top:4px}
  .go:hover{background:#0369a1}
  .go:disabled{opacity:.5;cursor:wait}
  #status{margin-top:14px;padding:12px;border-radius:8px;font-size:.9rem;display:none}
  #status.ok{display:block;background:#052e16;border:1px solid #166534;color:#86efac}
  #status.err{display:block;background:#2e0505;border:1px solid #7f1d1d;color:#fca5a5}
  #status.run{display:block;background:#0c1a2e;border:1px solid #1e40af;color:#93c5fd}
  .dl{display:none;margin-top:14px}
  .dl a{display:inline-block;padding:12px 24px;background:#16a34a;color:#fff;text-decoration:none;border-radius:10px;font-weight:600;font-size:1rem}
  .dl a:hover{background:#15803d}
  .count{font-size:.8rem;color:#8b93a7;margin-top:6px}
</style>
</head>
<body>
<div class="wrap">
  <h1>Canva PDF Cleaner</h1>
  <p class="sub">Fixes text overlap when uploading to Canva. Optionally removes handwriting &amp; watermarks.</p>

  <div class="card">
    <h2>1. Choose PDF</h2>
    <select id="pdf"></select>
    <p class="count" id="count"></p>
  </div>

  <div class="card">
    <h2>2. What to remove</h2>
    <div class="preset">
      <button type="button" id="pAll" class="active">Remove all markers</button>
      <button type="button" id="pNone">Keep markers (Canva fix only)</button>
    </div>
    <div class="opts">
      <label class="opt">
        <input type="checkbox" id="ink" checked>
        <div>
          <div class="lbl">Handwriting / pen marks</div>
          <div class="desc">Red, blue, green ink and scribbles</div>
        </div>
      </label>
      <label class="opt">
        <input type="checkbox" id="marks" checked>
        <div>
          <div class="lbl">Watermarks</div>
          <div class="desc">"coaching center" text and cyan "e1" logo</div>
        </div>
      </label>
      <label class="opt">
        <input type="checkbox" id="hindi" checked>
        <div>
          <div class="lbl">Hindi text</div>
          <div class="desc">Removes Hindi lines (uncheck to keep Hindi as a stable image)</div>
        </div>
      </label>
    </div>
    <p class="count">Canva text-overlap fix is always applied (English stays editable).</p>
  </div>

  <div class="card">
    <h2>3. Clean &amp; download</h2>
    <button class="go" id="go">Clean PDF</button>
    <div id="status"></div>
    <div class="dl" id="dl"><a id="dlLink" href="#">Download V2 PDF</a></div>
  </div>
</div>
<script>
const sel = document.getElementById('pdf');
const cnt = document.getElementById('count');
const ink = document.getElementById('ink');
const marks = document.getElementById('marks');
const hindi = document.getElementById('hindi');
const pAll = document.getElementById('pAll');
const pNone = document.getElementById('pNone');
const go = document.getElementById('go');
const st = document.getElementById('status');
const dl = document.getElementById('dl');
const dlLink = document.getElementById('dlLink');

async function loadList() {
  const r = await fetch('/api/list');
  const files = await r.json();
  sel.innerHTML = files.map(f => `<option value="${encodeURIComponent(f)}">${f}</option>`).join('');
  cnt.textContent = files.length + ' PDF(s) in PDFS TO CLEAN folder';
}
loadList();

function setPreset(all) {
  pAll.classList.toggle('active', all);
  pNone.classList.toggle('active', !all);
  ink.checked = all;
  marks.checked = all;
  hindi.checked = all;
}
pAll.onclick = () => setPreset(true);
pNone.onclick = () => setPreset(false);
ink.onchange = marks.onchange = hindi.onchange = () => {
  pAll.classList.toggle('active', ink.checked && marks.checked && hindi.checked);
  pNone.classList.toggle('active', !ink.checked && !marks.checked && !hindi.checked);
};

go.onclick = async () => {
  const name = sel.value;
  if (!name) return;
  go.disabled = true;
  dl.style.display = 'none';
  st.className = 'run';
  st.textContent = 'Cleaning... (Hindi pages can take a few seconds)';
  try {
    const body = new URLSearchParams({ file: name, ink: ink.checked ? '1' : '0', marks: marks.checked ? '1' : '0', hindi: hindi.checked ? '1' : '0' });
    const r = await fetch('/api/clean', { method: 'POST', body });
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    st.className = 'ok';
    st.textContent = 'Done. ' + (j.pages || '') + ' pages cleaned.';
    dlLink.href = j.url;
    dlLink.download = j.filename;
    dl.style.display = 'block';
  } catch (e) {
    st.className = 'err';
    st.textContent = 'Error: ' + e.message;
  } finally {
    go.disabled = false;
  }
};
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj):
        import json
        self._send(200, json.dumps(obj), "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self._send(200, HTML)
        elif parsed.path == "/api/list":
            files = []
            if os.path.isdir(PDF_DIR):
                files = sorted(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
            self._json(files)
        elif parsed.path == "/api/download":
            q = parse_qs(parsed.query)
            name = unquote(q.get("file", [""])[0])
            safe = os.path.basename(name)
            path = os.path.join(OUT_DIR, safe)
            if not os.path.isfile(path):
                self._send(404, b"Not found")
                return
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send(404, b"Not found")

    def do_POST(self):
        if urlparse(self.path).path != "/api/clean":
            self._send(404, b"Not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        q = parse_qs(body)
        name = unquote(q.get("file", [""])[0])
        remove_ink = q.get("ink", ["1"])[0] == "1"
        remove_marks = q.get("marks", ["1"])[0] == "1"
        remove_hindi = q.get("hindi", ["1"])[0] == "1"
        safe = os.path.basename(name)
        src = os.path.join(PDF_DIR, safe)
        if not os.path.isfile(src):
            self._json({"error": "PDF not found: " + safe})
            return
        out_name = v2_name(safe)
        out_path = os.path.join(OUT_DIR, out_name)
        try:
            stats = clean_pdf(src, out_path, remove_ink=remove_ink, remove_marks=remove_marks, remove_hindi=remove_hindi)
            import urllib.parse
            url = "/api/download?file=" + urllib.parse.quote(out_name)
            self._json({"ok": True, "url": url, "filename": out_name,
                        "pages": stats.get("pages"), "removed": stats.get("removed")})
        except Exception as e:
            self._json({"error": str(e)})


def main():
    port = 8765
    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Canva PDF Cleaner running at {url}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
