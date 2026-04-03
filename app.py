import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile

from flask import (
    Flask,
    after_this_request,
    jsonify,
    render_template_string,
    request,
    send_file,
)

from main import download_resources, parse_curl_command

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024

jobs_lock = threading.Lock()
jobs: dict = {}

PAGE = """
<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Downloader Dashboard</title>
  <style>
    :root {
      --bg: #0b1220;
      --panel: #111a2d;
      --panel-2: #0f1728;
      --text: #e8eefb;
      --muted: #93a4c3;
      --line: #2b3853;
      --brand: #3b82f6;
      --brand-2: #2563eb;
      --ok: #10b981;
      --warn: #f59e0b;
      --danger: #ef4444;
      --shadow: 0 12px 28px rgba(0, 0, 0, 0.35);
      font-family: Inter, Segoe UI, system-ui, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at 10% -10%, #1b2b4f 0%, var(--bg) 45%);
      color: var(--text);
      min-height: 100vh;
      padding: 28px 14px;
    }
    .app {
      max-width: 960px;
      margin: 0 auto;
      display: grid;
      gap: 14px;
    }
    .card {
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: var(--shadow);
      padding: 16px;
    }
    .hero {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    h1 {
      margin: 0;
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: .1px;
    }
    .sub {
      color: var(--muted);
      margin-top: 6px;
      font-size: .93rem;
      line-height: 1.45;
      max-width: 760px;
    }
    .badges { display: flex; gap: 8px; flex-wrap: wrap; }
    .badge {
      border: 1px solid #33476d;
      background: #172544;
      color: #bfd3ff;
      border-radius: 999px;
      font-size: .76rem;
      padding: 4px 10px;
      white-space: nowrap;
    }
    label {
      display: block;
      margin-bottom: 8px;
      font-weight: 600;
      font-size: .92rem;
    }
    textarea {
      width: 100%;
      min-height: 184px;
      background: #0b1220;
      color: #dbe8ff;
      border: 1px solid #2f4368;
      border-radius: 10px;
      padding: 12px;
      font-size: .84rem;
      line-height: 1.45;
      outline: none;
      transition: border-color .2s ease, box-shadow .2s ease;
      resize: vertical;
    }
    textarea:focus {
      border-color: #4f7ed7;
      box-shadow: 0 0 0 3px rgba(79, 126, 215, 0.2);
    }
    .row {
      margin-top: 10px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .hint {
      color: var(--muted);
      font-size: .86rem;
      margin: 0;
      max-width: 700px;
      line-height: 1.4;
    }
    button {
      border: none;
      background: linear-gradient(180deg, var(--brand), var(--brand-2));
      color: #fff;
      font-weight: 700;
      border-radius: 10px;
      padding: 10px 16px;
      cursor: pointer;
      letter-spacing: .1px;
      transition: transform .08s ease, opacity .2s ease, box-shadow .2s ease;
      box-shadow: 0 10px 18px rgba(37, 99, 235, 0.28);
    }
    button:hover { transform: translateY(-1px); }
    button:disabled { opacity: .55; cursor: not-allowed; transform: none; box-shadow: none; }

    .err {
      display: none;
      border: 1px solid #5b2230;
      background: rgba(127, 29, 29, .23);
      color: #fecaca;
      padding: 10px 12px;
      border-radius: 10px;
      white-space: pre-wrap;
      font-size: .88rem;
    }

    .panel { display: none; }
    .panel.active { display: block; }
    .status-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 8px;
    }
    .status-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid #2f4368;
      background: #0f1a31;
      color: #c5d8ff;
      border-radius: 999px;
      padding: 5px 11px;
      font-size: .8rem;
      font-weight: 600;
    }
    .status-chip::before {
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #60a5fa;
    }
    .status-chip.ok::before { background: var(--ok); }
    .status-chip.warn::before { background: var(--warn); }
    .status-chip.err::before { background: var(--danger); }
    .stats {
      font-size: .93rem;
      color: #dbe8ff;
      font-weight: 600;
    }
    .bar-wrap {
      height: 10px;
      background: #0a1325;
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid #2d4268;
    }
    .bar {
      height: 100%;
      background: linear-gradient(90deg, #3b82f6, #22d3ee);
      width: 0%;
      transition: width .2s ease;
    }
    .bar-meta {
      margin-top: 6px;
      color: var(--muted);
      font-size: .8rem;
      display: flex;
      justify-content: flex-end;
    }
    .metric-grid {
      margin-top: 10px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid #2c4065;
      background: #0d1730;
      border-radius: 10px;
      padding: 10px;
    }
    .metric .k {
      color: var(--muted);
      font-size: .75rem;
      margin-bottom: 4px;
    }
    .metric .v {
      color: #f1f5ff;
      font-size: 1.05rem;
      font-weight: 700;
    }
    .log-head {
      margin-top: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: #c4d3f1;
      font-size: .84rem;
    }
    .link-btn {
      border: 1px solid #35507f;
      background: transparent;
      box-shadow: none;
      font-weight: 600;
      padding: 6px 10px;
      font-size: .78rem;
    }
    .log {
      margin-top: 6px;
      background: #0a1325;
      border: 1px solid #2c4065;
      border-radius: 10px;
      padding: 10px 11px;
      font-size: .78rem;
      line-height: 1.5;
      max-height: 260px;
      overflow-y: auto;
      color: #c9d9f7;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .log div { white-space: pre-wrap; word-break: break-all; }
    .ok {
      border: 1px solid #1f5c4d;
      background: rgba(16, 185, 129, .12);
      color: #bbf7d0;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: .9rem;
      line-height: 1.45;
    }
    .ok ul { margin: 8px 0 0 18px; padding: 0; }
    code {
      font-family: ui-monospace, monospace;
      background: rgba(148, 163, 184, .14);
      border: 1px solid #3a4a66;
      border-radius: 5px;
      padding: 1px 5px;
      font-size: .8em;
    }
    @media (max-width: 780px) {
      .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <div class="app">
    <section class="card hero">
      <div>
        <h1>Smart Resource Downloader</h1>
        <p class="sub">Dán một lệnh <code>curl</code>, hệ thống sẽ tự nhận diện kiểu dữ liệu (Wallpics / ThemeKit / Sticker packs), tải, giải nén và đóng gói ZIP cho bạn.</p>
      </div>
      <div class="badges">
        <span class="badge">Wallpapers</span>
        <span class="badge">ThemeKit</span>
        <span class="badge">Sticker Packs</span>
      </div>
    </section>

    <section class="card">
      <form id="f">
        <label for="curl">Lệnh curl</label>
        <textarea id="curl" name="curl" placeholder='curl -H "Host: ..." ... "https://..."' required></textarea>
        <div class="row">
          <p class="hint">Sticker packs: chỉ tải file zip từ <code>sticker</code>. ThemeKit: tải assets + giải nén zip. Wallpapers: tải <code>wallpaper/upscaled/thumbnail</code>.</p>
          <button type="submit" id="btn">Bắt đầu tải & tạo ZIP</button>
        </div>
      </form>
      <p id="parseErr" class="err"></p>
    </section>

    <section id="progressPanel" class="card panel">
      <div class="status-top">
        <div class="status-chip warn" id="statusChip">Đang khởi tạo</div>
        <div class="stats" id="statsLine">—</div>
      </div>
      <div class="bar-wrap"><div class="bar" id="bar"></div></div>
      <div class="bar-meta"><span id="pctText">0%</span></div>
      <div class="metric-grid">
        <div class="metric"><div class="k">Đã xử lý</div><div class="v" id="mCurrent">0</div></div>
        <div class="metric"><div class="k">Tổng task</div><div class="v" id="mTotal">0</div></div>
        <div class="metric"><div class="k">Pha hiện tại</div><div class="v" id="mPhase">init</div></div>
        <div class="metric"><div class="k">Trạng thái</div><div class="v" id="mState">running</div></div>
      </div>
      <div class="log-head">
        <strong>Nhật ký xử lý</strong>
        <button type="button" class="link-btn" id="clearLogBtn">Xóa log</button>
      </div>
      <div class="log" id="log"></div>
    </section>

    <section id="donePanel" class="card panel">
      <div class="ok" id="doneMsg"></div>
    </section>
  </div>

  <script>
(function() {
  var f = document.getElementById("f");
  var btn = document.getElementById("btn");
  var curlEl = document.getElementById("curl");
  var parseErr = document.getElementById("parseErr");
  var progressPanel = document.getElementById("progressPanel");
  var donePanel = document.getElementById("donePanel");
  var statsLine = document.getElementById("statsLine");
  var bar = document.getElementById("bar");
  var pctText = document.getElementById("pctText");
  var statusChip = document.getElementById("statusChip");
  var mCurrent = document.getElementById("mCurrent");
  var mTotal = document.getElementById("mTotal");
  var mPhase = document.getElementById("mPhase");
  var mState = document.getElementById("mState");
  var logEl = document.getElementById("log");
  var doneMsg = document.getElementById("doneMsg");
  var clearLogBtn = document.getElementById("clearLogBtn");
  var pollTimer = null;
  var maxLog = 120;
  var lastSeenLog = "";
  var autoDownloadDone = false;

  function logLine(text) {
    var d = document.createElement("div");
    d.textContent = text;
    logEl.appendChild(d);
    while (logEl.children.length > maxLog) logEl.removeChild(logEl.firstChild);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setBar(cur, tot) {
    var pct = tot > 0 ? Math.round((cur / tot) * 100) : 0;
    bar.style.width = pct + "%";
    pctText.textContent = pct + "%";
    mCurrent.textContent = String(cur || 0);
    mTotal.textContent = String(tot || 0);
  }

  function setStatus(kind, text) {
    statusChip.className = "status-chip " + kind;
    statusChip.textContent = text;
  }

  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function triggerDownload(jobId) {
    var a = document.createElement("a");
    a.href = "/api/download/" + jobId;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  clearLogBtn.addEventListener("click", function() {
    logEl.innerHTML = "";
    lastSeenLog = "";
  });

  f.addEventListener("submit", function(ev) {
    ev.preventDefault();
    parseErr.style.display = "none";
    parseErr.textContent = "";
    donePanel.classList.remove("active");
    doneMsg.textContent = "";
    logEl.innerHTML = "";
    lastSeenLog = "";
    autoDownloadDone = false;
    progressPanel.classList.add("active");

    var curl = curlEl.value.trim();
    if (!curl) return;

    btn.disabled = true;
    btn.textContent = "Đang chuẩn bị…";
    statsLine.textContent = "Đang gửi yêu cầu…";
    mPhase.textContent = "init";
    mState.textContent = "running";
    setStatus("warn", "Đang khởi tạo job");
    setBar(0, 1);

    fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ curl: curl })
    })
    .then(function(r) { return r.json().then(function(j) { return { ok: r.ok, j: j }; }); })
    .then(function(x) {
      if (!x.ok || x.j.error) {
        throw new Error(x.j.error || "Không khởi tạo được job.");
      }
      return x.j.job_id;
    })
    .then(function(jobId) {
      btn.textContent = "Đang tải…";
      stopPoll();
      pollTimer = setInterval(function() {
        fetch("/api/progress/" + jobId).then(function(r) { return r.json(); }).then(function(p) {
          var phase = p.phase || "";
          var state = p.status || "running";
          var cur = p.current|0;
          var tot = p.total|0;
          mPhase.textContent = phase || "-";
          mState.textContent = state;
          if (phase === "api" || phase === "parse") {
            statsLine.textContent = p.message || "—";
            setStatus("warn", "Đang chuẩn bị dữ liệu");
            setBar(0, 1);
          } else if (phase === "download") {
            statsLine.textContent = "Đã tải " + cur + " / " + tot + " file";
            setStatus("warn", "Đang tải tài nguyên");
            setBar(cur, tot);
          } else if (phase === "unzip") {
            statsLine.textContent = p.message || "Đang giải nén…";
            setStatus("warn", "Đang giải nén");
            var uc = p.current|0;
            var ut = p.total|0;
            if (ut > 0) setBar(uc, ut);
            else setBar(1, 1);
          } else if (phase === "zipping") {
            statsLine.textContent = p.message || "Đang nén ZIP…";
            setStatus("warn", "Đang đóng gói ZIP");
            setBar(1, 1);
          }
          if (p.log_line && p.log_line !== lastSeenLog) {
            lastSeenLog = p.log_line;
            logLine(p.log_line);
          }

          if (p.status === "error") {
            stopPoll();
            btn.disabled = false;
            btn.textContent = "Bắt đầu tải & tạo ZIP";
            setStatus("err", "Job thất bại");
            parseErr.textContent = p.error || "Lỗi không xác định.";
            parseErr.style.display = "block";
            return;
          }
          if (p.status === "done") {
            stopPoll();
            if (autoDownloadDone) return;
            autoDownloadDone = true;
            btn.disabled = false;
            btn.textContent = "Bắt đầu tải & tạo ZIP";
            setStatus("ok", "Hoàn tất");
            setBar(1, 1);
            var kind = p.kind || "wallpics";
            var folderLine = kind === "themekit"
              ? "Số thư mục theme (theo name trong resourceList)"
              : kind === "stickers"
              ? "Số thư mục sticker pack"
              : "Số thư mục wallpaper (slug)";
            var html = "<strong>Hoàn tất.</strong> Loại: <strong>" + escapeHtml(kind) + "</strong><ul>";
            html += "<li>" + folderLine + ": <strong>" + (p.slug_count|0) + "</strong></li>";
            html += "<li>File tải thành công: <strong>" + (p.files_ok|0) + "</strong></li>";
            html += "<li>Không tải được / bỏ qua: <strong>" + (p.files_fail|0) + "</strong></li>";
            html += "<li>Giải nén ZIP: <strong>" + (p.unzip_ok|0) + "</strong> OK, <strong>" + (p.unzip_fail|0) + "</strong> lỗi</li>";
            if (p.zip_name) html += "<li>File nén tải về: <strong>" + escapeHtml(p.zip_name) + "</strong></li>";
            if (kind === "themekit") {
              html += "<li>Trong ZIP: <code>theme/&lt;tên&gt;/</code> — thumb, file .zip gói, thư mục previewLongList và previewShortList.</li>";
            }
            if (kind === "stickers") {
              html += "<li>Trong ZIP: <code>stickers/&lt;pack&gt;/</code> — chỉ file .zip từ trường <code>sticker</code>.</li>";
            }
            html += "</ul>";
            doneMsg.innerHTML = html;
            donePanel.classList.add("active");
            triggerDownload(jobId);
          }
        }).catch(function() {});
      }, 400);
    })
    .catch(function(e) {
      btn.disabled = false;
      btn.textContent = "Bắt đầu tải & tạo ZIP";
      setStatus("err", "Khởi tạo thất bại");
      parseErr.textContent = e.message || String(e);
      parseErr.style.display = "block";
      progressPanel.classList.remove("active");
    });
  });

  function escapeHtml(s) {
    var t = document.createElement("span");
    t.textContent = s;
    return t.innerHTML;
  }
})();
  </script>
</body>
</html>
"""


def _zip_dir(folder: str, zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(folder):
            for name in files:
                full = os.path.join(root, name)
                arc = os.path.relpath(full, folder)
                zf.write(full, arc)


def _update_job(job_id: str, **kwargs) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(kwargs)


def _run_job(job_id: str, api_url: str, headers: dict) -> None:
    tmp = tempfile.mkdtemp(prefix="wallpics_dl_")
    last_log = ""

    def on_progress(payload: dict) -> None:
        nonlocal last_log
        phase = payload.get("phase", "")
        msg = payload.get("message", "")
        if msg and msg != last_log:
            last_log = msg
            _update_job(
                job_id,
                phase=phase,
                current=payload.get("current", 0),
                total=payload.get("total", 0),
                log_line=msg,
            )
        else:
            _update_job(
                job_id,
                phase=phase,
                current=payload.get("current", 0),
                total=payload.get("total", 0),
            )

    try:
        with jobs_lock:
            jobs[job_id]["tmp_dir"] = tmp
            jobs[job_id]["status"] = "running"

        out = download_resources(api_url, headers, tmp, progress_callback=on_progress)

        if not out["ok"]:
            err = out.get("error") or "Tải thất bại."
            _update_job(job_id, status="error", error=err, phase="error", log_line=err)
            shutil.rmtree(tmp, ignore_errors=True)
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id]["tmp_dir"] = None
            return

        slug_n = out.get("slug_count", 0)
        if slug_n == 0:
            _update_job(
                job_id,
                status="error",
                error="Không có thư mục dữ liệu nào (Wallpics: slug; ThemeKit: theme; Sticker: stickers). Kiểm tra token / URL.",
                phase="error",
            )
            shutil.rmtree(tmp, ignore_errors=True)
            with jobs_lock:
                jobs[job_id]["tmp_dir"] = None
            return

        kind = out.get("kind") or "wallpics"
        _update_job(
            job_id,
            phase="zipping",
            current=1,
            total=1,
            log_line="Đang tạo file ZIP…",
        )

        if kind == "themekit":
            zip_prefix = "theme_"
        elif kind == "stickers":
            zip_prefix = "stickers_"
        else:
            zip_prefix = "wallpapers_"
        zip_fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix=zip_prefix)
        os.close(zip_fd)
        try:
            _zip_dir(tmp, zip_path)
        except Exception as e:
            try:
                os.remove(zip_path)
            except OSError:
                pass
            _update_job(
                job_id, status="error", error=f"Lỗi nén ZIP: {e}", phase="error"
            )
            shutil.rmtree(tmp, ignore_errors=True)
            with jobs_lock:
                jobs[job_id]["tmp_dir"] = None
            return

        if kind == "themekit":
            zip_name = f"theme_{int(time.time())}.zip"
        elif kind == "stickers":
            zip_name = f"stickers_{int(time.time())}.zip"
        else:
            zip_name = f"wallpapers_{int(time.time())}.zip"
        with jobs_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["zip_path"] = zip_path
            jobs[job_id]["zip_name"] = zip_name
            jobs[job_id]["slug_count"] = out.get("slug_count", 0)
            jobs[job_id]["files_ok"] = out.get("files_ok", 0)
            jobs[job_id]["files_fail"] = out.get("files_fail", 0)
            jobs[job_id]["unzip_ok"] = out.get("unzip_ok", 0)
            jobs[job_id]["unzip_fail"] = out.get("unzip_fail", 0)
            jobs[job_id]["kind"] = kind
            jobs[job_id]["phase"] = "done"
            jobs[job_id]["log_line"] = (
                f"Xong: tải {out.get('files_ok', 0)} OK, {out.get('files_fail', 0)} lỗi; "
                f"giải nén {out.get('unzip_ok', 0)} OK, {out.get('unzip_fail', 0)} lỗi. ZIP: {zip_name}"
            )
    except Exception as e:
        err = str(e)
        _update_job(job_id, status="error", error=err, phase="error", log_line=err)
        shutil.rmtree(tmp, ignore_errors=True)
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["tmp_dir"] = None


@app.route("/", methods=["GET"])
def index():
    return render_template_string(PAGE)


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}
    curl_val = (data.get("curl") or "").strip()
    if not curl_val:
        return jsonify({"error": "Thiếu nội dung curl."}), 400
    try:
        api_url, headers = parse_curl_command(curl_val)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "status": "running",
            "phase": "init",
            "current": 0,
            "total": 0,
            "error": None,
            "tmp_dir": None,
            "zip_path": None,
            "zip_name": None,
            "slug_count": 0,
            "files_ok": 0,
            "files_fail": 0,
            "unzip_ok": 0,
            "unzip_fail": 0,
            "log_line": None,
            "kind": None,
        }

    t = threading.Thread(target=_run_job, args=(job_id, api_url, headers), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>")
def api_progress(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job không tồn tại."}), 404
        snap = {k: job.get(k) for k in job}

    body = {
        "status": snap["status"],
        "phase": snap.get("phase"),
        "current": snap.get("current", 0),
        "total": snap.get("total", 0),
        "error": snap.get("error"),
        "log_line": snap.get("log_line"),
    }
    if snap["status"] == "done":
        body["slug_count"] = snap.get("slug_count", 0)
        body["files_ok"] = snap.get("files_ok", 0)
        body["files_fail"] = snap.get("files_fail", 0)
        body["zip_name"] = snap.get("zip_name")
        body["kind"] = snap.get("kind")
        body["unzip_ok"] = snap.get("unzip_ok", 0)
        body["unzip_fail"] = snap.get("unzip_fail", 0)
    return jsonify(body)


@app.route("/api/download/<job_id>")
def api_download(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("status") != "done":
            return jsonify({"error": "Chưa sẵn sàng hoặc đã hết hạn."}), 404
        zip_path = job.get("zip_path")
        zip_name = job.get("zip_name") or "wallpapers.zip"
        tmp_dir = job.get("tmp_dir")

    if not zip_path or not os.path.isfile(zip_path):
        return jsonify({"error": "File ZIP không còn."}), 404

    @after_this_request
    def cleanup(resp):
        try:
            os.remove(zip_path)
        except OSError:
            pass
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        with jobs_lock:
            jobs.pop(job_id, None)
        return resp

    return send_file(
        zip_path,
        as_attachment=True,
        download_name=zip_name,
        mimetype="application/zip",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "4090"))
    # Tắt debug khi deploy (Railway đặt RAILWAY_ENVIRONMENT)
    debug = os.environ.get("RAILWAY_ENVIRONMENT") is None
    app.run(host="0.0.0.0", port=port, debug=debug)
