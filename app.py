#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3DGS 网页控制台 —— Windows 侧入口

用法:
    python app.py            # 启动后自动打开 http://127.0.0.1:8000

流程:
    浏览器上传视频 -> data/inbox/<名称>.<ext>
    点击"开始训练"   -> 调 WSL 里的 run_job.sh（抽帧/COLMAP/训练/导出PLY）
    页面实时显示阶段进度（读 logs/<名称>.status / .progress）
    完成后:
        [网页漫游]   加载 web/assets/<名称>/splat.ply 到内置 WebGL 播放器
        [官方 Viewer] 在 WSL 里启动 ns-viewer，浏览器打开 http://localhost:7007
        [预览视频]   renders/<名称>/preview.mp4

只依赖 Python 标准库。
"""
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

# ---------------- 路径与常量 ----------------
ROOT = os.path.dirname(os.path.abspath(__file__))          # D:\job\3DGS
WSL_ROOT = "/mnt/" + ROOT[0].lower() + ROOT[2:].replace("\\", "/")   # /mnt/d/job/3DGS
WSL_DISTRO = "Ubuntu"
INBOX = os.path.join(ROOT, "data", "inbox")
LOGDIR = os.path.join(ROOT, "logs")
HOST, PORT = "127.0.0.1", 8000
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
STAGES = ["gsplat", "extract", "colmap", "train", "export", "preview"]
STAGE_LABEL = {
    "gsplat": "CUDA 扩展", "extract": "抽帧", "colmap": "位姿求解",
    "train": "训练", "export": "导出网页模型", "preview": "预览渲染",
    "done": "完成", "error": "出错",
}

for d in (INBOX, LOGDIR):
    os.makedirs(d, exist_ok=True)

STATE_FILE = os.path.join(ROOT, "app_state.json")

# ---------------- 任务状态 ----------------
_lock = threading.Lock()
JOBS = {}          # name -> dict
_RUNNING = None    # 当前运行中的任务名


def load_state():
    global JOBS
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                JOBS = json.load(f)
        except Exception:
            JOBS = {}
    # app.py 重启前如果有任务在跑，标记为中断
    for j in JOBS.values():
        if j.get("state") == "running":
            j["state"] = "error"
            j["detail"] = "app.py 重启导致中断"


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(JOBS, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def new_job(name, video, iters, fps):
    j = {
        "name": name, "video": video, "iters": iters, "fps": fps,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "state": "queued", "stage": "", "detail": "", "progress": 0.0,
        "config": "", "error": "",
    }
    JOBS[name] = j
    save_state()
    return j


def job_paths(name):
    """返回该任务相关的 Windows 路径"""
    return {
        "data": os.path.join(ROOT, "data", name),
        "out": os.path.join(ROOT, "outputs", name),
        "asset": os.path.join(ROOT, "web", "assets", name),
        "render": os.path.join(ROOT, "renders", name),
        "status": os.path.join(LOGDIR, name + ".status"),
        "progress": os.path.join(LOGDIR, name + ".progress"),
        "log": os.path.join(LOGDIR, name + ".log"),
    }


def wsl_path(win_path):
    p = os.path.abspath(win_path)
    return "/mnt/" + p[0].lower() + p[2:].replace("\\", "/")


def win_path(wsl_p):
    """WSL 路径 /mnt/d/a/b -> Windows 路径 D:\\a\\b；非 /mnt/ 开头的原样返回"""
    if wsl_p and wsl_p.startswith("/mnt/") and len(wsl_p) > 6 and wsl_p[6] != "":
        return wsl_p[5].upper() + ":" + wsl_p[6:].replace("/", "\\")
    return wsl_p


def wsl(args, timeout=60):
    """在 WSL 中执行命令，返回 (rc, stdout)"""
    cmd = ["wsl.exe", "-d", WSL_DISTRO, "--"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or b"").decode("utf-8", "replace")
        return r.returncode, out
    except Exception as e:
        return -1, str(e)


# ---------------- 任务执行线程 ----------------
def job_thread(name, video_win, iters, fps):
    global _RUNNING
    p = job_paths(name)
    j = JOBS[name]
    status_file, prog_file = p["status"], p["progress"]
    video_wsl = wsl_path(video_win)

    try:
        proc = subprocess.Popen(
            ["wsl.exe", "-d", WSL_DISTRO, "--", "bash", wsl_path(os.path.join(ROOT, "run_job.sh")),
             name, video_wsl, str(iters), str(fps)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        j["state"] = "running"

        while proc.poll() is None:
            # 读 WSL 写出的状态与进度
            try:
                with open(status_file, encoding="utf-8", errors="replace") as f:
                    txt = f.read().strip()
                if "|" in txt:
                    stage, detail = txt.split("|", 1)
                    j["stage"], j["detail"] = stage, detail
            except Exception:
                pass
            try:
                with open(prog_file) as f:
                    j["progress"] = min(100.0, float(f.read().strip() or 0))
            except Exception:
                pass
            time.sleep(1.0)

        rc = proc.returncode
        # 收尾状态
        stage, detail = "", ""
        try:
            with open(status_file, encoding="utf-8", errors="replace") as f:
                txt = f.read().strip()
            if "|" in txt:
                stage, detail = txt.split("|", 1)
        except Exception:
            pass

        if rc == 0 and stage == "done":
            j["state"] = "done"
            j["stage"] = "done"
            j["detail"] = "全部完成"
            j["progress"] = 100.0
            j["config"] = detail            # run_job.sh 把 config.yml 路径写在这里
        else:
            j["state"] = "error"
            j["stage"] = stage or "error"
            j["detail"] = detail or ("进程退出码 %s，详见日志" % rc)
    except Exception as e:
        j["state"] = "error"
        j["detail"] = "内部错误: %s" % e
    finally:
        save_state()
        _RUNNING = None


def start_job(name, iters=None, fps=None):
    global _RUNNING
    j = JOBS.get(name)
    if not j:
        return False, "任务不存在"
    if _RUNNING:
        return False, "已有任务在运行: %s" % _RUNNING
    if j["state"] == "running":
        return False, "该任务已在运行"

    video_win = os.path.join(INBOX, j["video"])
    if not os.path.exists(video_win):
        return False, "视频文件不存在: %s" % j["video"]

    iters = int(iters or j.get("iters") or 20000)
    fps = int(fps or j.get("fps") or 6)
    j["iters"], j["fps"] = iters, fps

    # 重置状态文件，避免读到上一次的残留
    for f in (job_paths(name)["status"], job_paths(name)["progress"]):
        try:
            os.remove(f)
        except OSError:
            pass

    _RUNNING = name
    threading.Thread(target=job_thread, args=(name, video_win, iters, fps), daemon=True).start()
    return True, "已开始"


def delete_job(name):
    if name == _RUNNING:
        return False, "任务正在运行，不能删除"
    j = JOBS.pop(name, None)
    if not j:
        return False, "任务不存在"
    p = job_paths(name)
    for d in ("data", "out", "asset", "render"):
        shutil.rmtree(p[d], ignore_errors=True)
    for f in ("status", "progress", "log"):
        try:
            os.remove(p[f])
        except OSError:
            pass
    save_state()
    return True, "已删除"


# ---------------- 历史模型导入与按需导出 ----------------
_EXPORTING = set()      # 正在导出 PLY 的任务名


def get_pybin():
    """WSL 里 nerfstudio 的 bin 目录（启动时探测一次）"""
    global _PYBIN
    if _PYBIN is None:
        rc, out = wsl(["bash", "-lc", "echo $HOME"], timeout=30)
        home = out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else ""
        _PYBIN = (home + "/envs/nerfstudio/bin") if home else "/home/fjy001/envs/nerfstudio/bin"
    return _PYBIN


_PYBIN = None


def find_models():
    """扫描 outputs*/ 下的 splatfacto/<时间戳>/config.yml，返回 {任务名: 最新config路径}"""
    found = {}
    for base in sorted(os.listdir(ROOT)):
        if not base.startswith("outputs"):
            continue
        b = os.path.join(ROOT, base)
        if not os.path.isdir(b):
            continue
        for dirpath, _dirs, files in os.walk(b):
            if "config.yml" not in files:
                continue
            if os.path.basename(os.path.dirname(dirpath)) != "splatfacto":
                continue
            name = os.path.basename(os.path.dirname(os.path.dirname(dirpath)))
            # 目录名形如 2026-10-14_152713，取最新的那个
            if name not in found or dirpath > found[name][0]:
                found[name] = (dirpath, os.path.join(dirpath, "config.yml"))
    return found


def _read_iters(cfg):
    try:
        with open(cfg, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("max_num_iterations:"):
                    return int(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return 0


def import_history():
    """把磁盘上已训练好的模型登记成任务，让控制台能看到并打开"""
    for name, (_dirpath, cfg) in find_models().items():
        if not NAME_RE.match(name):
            continue
        iters = _read_iters(cfg)
        j = JOBS.get(name)
        if j is None:
            j = new_job(name, "", iters, 6)
            j["video"] = "历史模型"
        # 只升级状态，不降级（避免覆盖正在跑/已完成的记录）
        if j.get("state") in ("queued", "error", "running"):
            j["state"] = "done"
            j["stage"] = "done"
            j["detail"] = "历史训练产物"
            j["progress"] = 100.0
        if not j.get("config"):
            j["config"] = wsl_path(cfg)
        if iters:
            j["iters"] = iters
        j["has_ply"] = os.path.exists(os.path.join(ROOT, "web", "assets", name, "splat.ply"))
        j["has_preview"] = os.path.exists(os.path.join(ROOT, "renders", name, "preview.mp4"))
    save_state()


def export_ply(name):
    j = JOBS.get(name)
    if not j or not j.get("config"):
        return False, "没有可导出的模型"
    if name in _EXPORTING:
        return False, "正在导出中"
    if _RUNNING:
        return False, "有任务正在训练，等它跑完再导出（显存不够）"
    _EXPORTING.add(name)
    threading.Thread(target=_export_thread, args=(name,), daemon=True).start()
    return True, "导出已开始，通常 1-3 分钟"


def _export_thread(name):
    j = JOBS[name]
    outdir = os.path.join(ROOT, "web", "assets", name)
    os.makedirs(outdir, exist_ok=True)
    j["detail"] = "正在导出网页模型…"
    rc, out = wsl([
        os.path.join(get_pybin(), "ns-export"), "gaussian-splat",
        "--load-config", j["config"],
        "--output-dir", wsl_path(outdir),
        "--output-filename", "splat.ply",
        "--ply-color-mode", "rgb",
    ], timeout=3600)
    ply = os.path.join(outdir, "splat.ply")
    if rc == 0 and os.path.exists(ply):
        j["has_ply"] = True
        j["detail"] = "全部完成"
    else:
        j["detail"] = "导出失败: " + (out.strip()[-300:] or ("rc=%s" % rc))
    _EXPORTING.discard(name)
    save_state()


def viewer_running():
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", 7007))
        return True
    except Exception:
        return False
    finally:
        s.close()


def tail_log(path, lines=20, max_bytes=65536):
    """读取日志文件末尾若干行，用于失败时给用户看报错。"""
    if not path or not os.path.exists(path):
        return "(无日志)"
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            n = fh.tell()
            fh.seek(max(0, n - max_bytes))
            data = fh.read().decode("utf-8", "replace")
        return "\n".join(data.splitlines()[-lines:]) or "(日志为空)"
    except Exception as e:
        return "(读取日志失败: %s)" % e


# viewer 必须用 Popen 保活：viewer.sh 里是前台 exec ns-viewer，
# 只有 wsl.exe 进程一直活着，WSL 才不会把 viewer 当残留进程收走。
_VIEWER_PROC = None
_VIEWER_LOG = None


def start_viewer(name):
    """启动官方 viewer。wsl.exe 的存活期 == viewer 的存活期，故不能用 run() 等待。"""
    global _VIEWER_PROC, _VIEWER_LOG
    j = JOBS.get(name)
    if not j or j["state"] != "done" or not j.get("config"):
        return False, "该任务没有已完成的模型"
    if not j.get("config") or not os.path.exists(win_path(j["config"])):
        return False, "找不到 config.yml，模型可能已删除"

    if _VIEWER_PROC and _VIEWER_PROC.poll() is None:
        try:
            _VIEWER_PROC.terminate()
            _VIEWER_PROC.wait(timeout=5)
        except Exception:
            try:
                _VIEWER_PROC.kill()
            except Exception:
                pass
    _VIEWER_PROC = None

    log_path = os.path.join(LOGDIR, "viewer.log")
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 2_000_000:
            os.remove(log_path)
    except Exception:
        pass
    try:
        _VIEWER_LOG = open(log_path, "ab")
    except Exception:
        _VIEWER_LOG = None

    try:
        _VIEWER_PROC = subprocess.Popen(
            ["wsl.exe", "-d", WSL_DISTRO, "--", "bash",
             wsl_path(os.path.join(ROOT, "viewer.sh")),
             # j["config"] 已是 WSL 路径，直接传，不能再包 wsl_path()
             "start", j["config"], "7007"],
            stdout=_VIEWER_LOG or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        if _VIEWER_LOG:
            _VIEWER_LOG.close()
            _VIEWER_LOG = None
        return False, "启动失败: %s" % e

    # 等端口就绪（加载模型约需 5~15 秒）
    for _ in range(60):
        if _VIEWER_PROC.poll() is not None:
            break
        if viewer_running():
            return True, "http://localhost:7007"
        time.sleep(0.5)

    err = tail_log(log_path, 12)
    stop_viewer()
    return False, "启动失败，日志尾部：\n%s" % err


def stop_viewer():
    global _VIEWER_PROC, _VIEWER_LOG
    if _VIEWER_PROC:
        try:
            _VIEWER_PROC.terminate()
            _VIEWER_PROC.wait(timeout=5)
        except Exception:
            try:
                _VIEWER_PROC.kill()
            except Exception:
                pass
        _VIEWER_PROC = None
    if _VIEWER_LOG:
        try:
            _VIEWER_LOG.close()
        except Exception:
            pass
        _VIEWER_LOG = None
    # 兜底：清掉 WSL 里可能残留的 ns-viewer
    wsl(["pkill", "-f", "ns-viewer"], timeout=20)
    return True, "已停止"


# ---------------- HTTP 服务 ----------------
MIME = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".ply": "application/octet-stream", ".mp4": "video/mp4", ".png": "image/png",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "3DGS-Console/1.0"

    # ---- 基础工具 ----
    def log_message(self, fmt, *args):
        # 只记录非轮询请求，且要容忍 send_error 传入的 HTTPStatus 对象
        try:
            first = args[0] if args else ""
            if not isinstance(first, str) or "/api/state" not in first:
                super().log_message(fmt, *args)
        except Exception:
            pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type=None, allow_range=True):
        if not os.path.isfile(path):
            self.send_json({"error": "文件不存在"}, 404)
            return
        size = os.path.getsize(path)
        ctype = content_type or MIME.get(os.path.splitext(path)[1].lower(),
                                         "application/octet-stream")
        rng = self.headers.get("Range") if allow_range else None
        m = re.match(r"bytes=(\d+)-(\d*)", rng or "")
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    # ---- 路由 ----
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(ROOT, "web", "index.html"))
        if path == "/health":
            return self.send_json({"ok": True})
        if path.startswith("/static/"):
            rel = os.path.normpath(path[len("/static/"):])
            if rel.startswith(".."):
                return self.send_json({"error": "bad path"}, 400)
            return self.send_file(os.path.join(ROOT, "web", "static", rel))

        if path == "/api/state":
            with _lock:
                jobs = [JOBS[n] for n in sorted(JOBS, key=lambda n: JOBS[n].get("created", ""),
                                                reverse=True)]
            return self.send_json({"jobs": jobs, "running": _RUNNING,
                                   "viewer": viewer_running(), "wsl_root": WSL_ROOT})

        if path == "/api/log":
            name = (qs.get("name") or [""])[0]
            if name not in JOBS:
                return self.send_json({"error": "任务不存在"}, 404)
            f = job_paths(name)["log"]
            tail = ""
            if os.path.exists(f):
                with open(f, "rb") as fh:
                    fh.seek(0, 2)
                    n = fh.tell()
                    fh.seek(max(0, n - 16000))
                    tail = fh.read().decode("utf-8", "replace")
            return self.send_json({"log": tail})

        if path.startswith("/assets/") and path.endswith("/splat.ply"):
            name = path.split("/")[2]
            if not NAME_RE.match(name):
                return self.send_json({"error": "bad name"}, 400)
            return self.send_file(os.path.join(ROOT, "web", "assets", name, "splat.ply"),
                                  allow_range=False)

        if path.startswith("/renders/") and path.endswith("/preview.mp4"):
            name = path.split("/")[2]
            if not NAME_RE.match(name):
                return self.send_json({"error": "bad name"}, 400)
            return self.send_file(os.path.join(ROOT, "renders", name, "preview.mp4"))

        return self.send_json({"error": "not found"}, 404)

    def do_HEAD(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        if path in ("/", "/index.html"):
            p = os.path.join(ROOT, "web", "index.html")
        elif path == "/health":
            return self.send_json({"ok": True})
        elif path.startswith("/static/"):
            rel = os.path.normpath(path[len("/static/"):])
            if rel.startswith(".."):
                return self.send_json({"error": "bad path"}, 400)
            p = os.path.join(ROOT, "web", "static", rel)
        elif path.startswith("/assets/") and path.endswith("/splat.ply"):
            name = path.split("/")[2]
            if not NAME_RE.match(name):
                return self.send_json({"error": "bad name"}, 400)
            p = os.path.join(ROOT, "web", "assets", name, "splat.ply")
        elif path.startswith("/renders/") and path.endswith("/preview.mp4"):
            name = path.split("/")[2]
            if not NAME_RE.match(name):
                return self.send_json({"error": "bad name"}, 400)
            p = os.path.join(ROOT, "renders", name, "preview.mp4")
        else:
            return self.send_json({"error": "not found"}, 404)
        if not os.path.isfile(p):
            return self.send_json({"error": "文件不存在"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(p)[1].lower(),
                                                  "application/octet-stream"))
        self.send_header("Content-Length", str(os.path.getsize(p)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        if u.path == "/api/upload":
            raw_name = unquote((qs.get("name") or [""])[0])
            if not raw_name:
                return self.send_json({"error": "缺少文件名"}, 400)
            ext = os.path.splitext(raw_name)[1].lower()
            if ext not in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"):
                return self.send_json({"error": "不支持的格式 %s" % ext}, 400)
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return self.send_json({"error": "空文件"}, 400)

            base = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(raw_name)[0]) or "job"
            name = base
            i = 2
            while name in JOBS:
                name = "%s_%d" % (base, i)
                i += 1

            dest = os.path.join(INBOX, name + ext)
            with open(dest, "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left > 0:
                try:
                    os.remove(dest)
                except OSError:
                    pass
                return self.send_json({"error": "上传中断"}, 400)

            new_job(name, name + ext, 20000, 6)
            return self.send_json({"name": name})

        if u.path == "/api/start":
            d = json.loads(self.read_body() or b"{}")
            name = d.get("name", "")
            if not NAME_RE.match(name):
                return self.send_json({"error": "非法任务名"}, 400)
            ok, msg = start_job(name, d.get("iters"), d.get("fps"))
            return self.send_json({"ok": ok, "msg": msg}, 200 if ok else 409)

        if u.path == "/api/delete":
            d = json.loads(self.read_body() or b"{}")
            ok, msg = delete_job(d.get("name", ""))
            return self.send_json({"ok": ok, "msg": msg}, 200 if ok else 409)

        if u.path == "/api/viewer/start":
            d = json.loads(self.read_body() or b"{}")
            ok, msg = start_viewer(d.get("name", ""))
            return self.send_json({"ok": ok, "msg": msg}, 200 if ok else 409)

        if u.path == "/api/viewer/stop":
            ok, msg = stop_viewer()
            return self.send_json({"ok": ok, "msg": msg})

        if u.path == "/api/export":
            d = json.loads(self.read_body() or b"{}")
            name = d.get("name", "")
            if not NAME_RE.match(name):
                return self.send_json({"error": "非法任务名"}, 400)
            ok, msg = export_ply(name)
            return self.send_json({"ok": ok, "msg": msg}, 200 if ok else 409)

        return self.send_json({"error": "not found"}, 404)


def main():
    load_state()
    import_history()      # 扫描磁盘上已训练好的模型，登记成任务
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://%s:%d" % (HOST, PORT)
    print("=" * 56)
    print("3DGS 网页控制台已启动")
    print("  地址:  %s" % url)
    print("  投放:  %s  或用网页上传" % INBOX)
    print("  WSL:   %s" % WSL_ROOT)
    print("  历史模型: %d 个" % sum(1 for j in JOBS.values() if j.get("state") == "done"))
    print("=" * 56)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
