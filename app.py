import os
import re
import shutil
import tempfile
import threading
import time
import uuid

from flask import Flask, render_template, request, jsonify, send_file, after_this_request
import yt_dlp

app = Flask(__name__)

COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")

# 진행 중인 작업들 {작업번호: {...}}
jobs = {}
jobs_lock = threading.Lock()


def update(job_id, **kw):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(kw)


def build_options(job_id, out_dir, mode, quality):
    def progress_hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            percent = round(done * 100 / total, 1) if total else 0
            speed = d.get("speed") or 0
            eta = d.get("eta")
            # 영상+소리를 따로 받을 때 어떤 단계인지 표시
            info = d.get("info_dict") or {}
            if info.get("vcodec") not in (None, "none") and info.get("acodec") in (None, "none"):
                stage = "영상 받는 중"
            elif info.get("vcodec") in (None, "none") and info.get("acodec") not in (None, "none"):
                stage = "소리 받는 중"
            else:
                stage = "받는 중"
            update(job_id, status="downloading", stage=stage, percent=percent,
                   speed=f"{speed / 1024 / 1024:.1f} MB/s" if speed else "",
                   eta=f"{int(eta)}초 남음" if eta else "")
        elif d["status"] == "finished":
            update(job_id, percent=100)

    def postprocessor_hook(d):
        if d["status"] == "started":
            update(job_id, status="processing", stage="파일 합치는 중", percent=100)

    opts = {
        "outtmpl": os.path.join(out_dir, "%(title).80s.%(ext)s"),
        "noplaylist": True,
        "quiet": False,          # 진행 기록을 Cloudtype 로그에 남김
        "verbose": True,
        "noprogress": True,      # 로그가 퍼센트 줄로 도배되지 않게
        "socket_timeout": 30,    # 30초 응답 없으면 포기하고 다음 방법으로
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "http_chunk_size": 10 * 1024 * 1024,  # 10MB씩 나눠 받기 (빈 파일·속도 제한 줄임)
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
    }
    if mode == "mp3":
        opts["format"] = "ba/b"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    elif mode in ("srt", "txt"):
        # 영상은 받지 않고 자막만
        opts["skip_download"] = True
        opts["ignore_no_formats_error"] = True
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["ko", "en"]
        opts["subtitlesformat"] = "vtt/srt/best"
    else:  # mp4, shorts
        h = quality if quality in (360, 480, 720, 1080) else 1080
        opts["format"] = f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
        opts["merge_output_format"] = "mp4"
    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    return opts


def vtt_to_srt(text):
    """VTT 자막을 SRT로 바꿈"""
    out, n = [], 0
    for block in text.replace("\r", "").split("\n\n"):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        times = lines[idx].split(" ")
        start_t, end_t = times[0].replace(".", ","), times[2].replace(".", ",")
        if start_t.count(":") == 1:
            start_t = "00:" + start_t
        if end_t.count(":") == 1:
            end_t = "00:" + end_t
        words = [re.sub(r"<[^>]+>", "", l) for l in lines[idx + 1:]]
        words = [w for w in words if w.strip()]
        if not words:
            continue
        n += 1
        out.append(f"{n}\n{start_t} --> {end_t}\n" + "\n".join(words))
    return "\n\n".join(out) + "\n"


def subs_to_text(text):
    """자막에서 시간 표시를 빼고 문장만 남김 (자동 자막의 반복 줄 제거)"""
    lines, prev = [], ""
    for l in text.replace("\r", "").split("\n"):
        l = re.sub(r"<[^>]+>", "", l).strip()
        if (not l or "-->" in l or l.isdigit() or l.startswith(("WEBVTT", "Kind:", "Language:"))):
            continue
        if l != prev:
            lines.append(l)
        prev = l
    return "\n".join(lines) + "\n"


def friendly_error(msg):
    if "자막이 없어요" in msg:
        return "이 영상에는 받을 수 있는 자막이 없어요."
    if "Sign in to confirm" in msg or "bot" in msg:
        return "유튜브가 이 서버를 로봇으로 판단해 막았어요. cookies.txt를 넣어 보세요."
    if "Requested format is not available" in msg:
        return "받을 수 있는 형식이 없어요. 다시 배포해서 yt-dlp를 최신으로 바꿔 보세요."
    if "empty" in msg or "403" in msg:
        return "유튜브가 이 서버의 다운로드를 막고 있어요. cookies.txt를 넣거나 내 컴퓨터에서 실행해 보세요."
    return "다운로드 중 문제가 생겼어요."


# 차례로 시도할 방법들 (앞의 방법이 막히면 다음 방법으로)
ATTEMPTS = [
    {"name": "기본 방법", "clients": None, "format": None},
    {"name": "TV 방식", "clients": ["tv", "web_safari"], "format": None},
    {"name": "모바일 방식", "clients": ["android_vr", "ios"], "format": None},
    {"name": "저화질 한 파일(360p)", "clients": None, "format": "18/b"},
]


def finish_subtitles(job_id, out_dir, mode, title):
    subs = [f for f in os.listdir(out_dir) if f.endswith((".vtt", ".srt"))]
    if not subs:
        raise RuntimeError("이 영상에는 한국어·영어 자막이 없어요.")
    subs.sort(key=lambda f: (0 if ".ko" in f else 1, f))  # 한국어 먼저
    with open(os.path.join(out_dir, subs[0]), encoding="utf-8", errors="ignore") as fp:
        raw = fp.read()
    safe = re.sub(r'[\\/:*?"<>|]', "", title or "자막")[:80]
    if mode == "srt":
        content = raw if subs[0].endswith(".srt") else vtt_to_srt(raw)
        name = f"{safe}.srt"
    else:
        content = subs_to_text(raw)
        name = f"{safe}_대본.txt"
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(content)
    return path, name


def run_job(job_id, target, mode, quality):
    out_dir = tempfile.mkdtemp()
    update(job_id, out_dir=out_dir)
    last_error = ""
    for i, attempt in enumerate(ATTEMPTS, start=1):
        for f in os.listdir(out_dir):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
        update(job_id, attempt_started=time.time())
        if i > 1:
            update(job_id, status="starting", percent=0, speed="", eta="",
                   stage=f"다른 방법으로 다시 시도 중 ({i}/{len(ATTEMPTS)}: {attempt['name']})")
        else:
            update(job_id, stage="영상 정보 확인 중… (30초~1분)")

        print(f"[{job_id}] ===== 시도 {i}/{len(ATTEMPTS)}: {attempt['name']} 시작 ({mode}) =====", flush=True)
        opts = build_options(job_id, out_dir, mode, quality)
        if attempt["clients"]:
            opts["extractor_args"] = {"youtube": {"player_client": attempt["clients"]}}
        if attempt["format"] and mode in ("mp4", "shorts"):
            opts["format"] = attempt["format"]
            opts.pop("merge_output_format", None)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
                if info and info.get("entries"):  # 검색어로 받은 경우
                    info = info["entries"][0]
                dur = info.get("duration") or 0
                update(job_id, title=info.get("title", ""),
                       thumbnail=info.get("thumbnail", ""),
                       channel=info.get("uploader") or info.get("channel") or "",
                       duration=f"{int(dur) // 3600}:{int(dur) % 3600 // 60:02d}:{int(dur) % 60:02d}" if dur else "",
                       stage="자막 받는 중" if mode in ("srt", "txt") else "받기 시작")
                ydl.process_ie_result(info, download=True)

            if mode in ("srt", "txt"):
                path, name = finish_subtitles(job_id, out_dir, mode, info.get("title"))
            else:
                files = [f for f in os.listdir(out_dir)
                         if not f.endswith((".part", ".ytdl"))
                         and os.path.getsize(os.path.join(out_dir, f)) > 0]
                if not files:
                    raise RuntimeError("The downloaded file is empty")
                name = files[0]
                path = os.path.join(out_dir, name)
            update(job_id, status="done", stage="완료!", percent=100, file_path=path, file_name=name)
            return
        except Exception as e:
            last_error = str(e)
            print(f"[{job_id}] 시도 {i} ({attempt['name']}) 실패: {last_error}", flush=True)
            if any(k in last_error for k in ("Private video", "Video unavailable",
                                              "is not a valid URL", "자막이 없어요")):
                break

    update(job_id, status="error", error=f"{friendly_error(last_error)} (자세히: {last_error})")
    shutil.rmtree(out_dir, ignore_errors=True)


def cleanup_old_jobs():
    """30분 지난 작업과 파일은 자동으로 지움"""
    while True:
        time.sleep(300)
        now = time.time()
        with jobs_lock:
            old = [k for k, v in jobs.items() if now - v["created"] > 1800]
            for k in old:
                d = jobs.pop(k).get("out_dir")
                if d:
                    shutil.rmtree(d, ignore_errors=True)


threading.Thread(target=cleanup_old_jobs, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(silent=True) or request.form
    query = (data.get("query") or "").strip()
    mode = data.get("mode") or ("mp3" if data.get("type") == "audio" else "mp4")
    if mode not in ("mp4", "shorts", "mp3", "srt", "txt"):
        mode = "mp4"
    try:
        quality = int(data.get("quality") or 1080)
    except (TypeError, ValueError):
        quality = 1080
    if not query:
        return jsonify(error="유튜브 주소나 검색어를 입력해 주세요."), 400

    target = query if query.startswith("http") else f"ytsearch1:{query}"
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"status": "starting", "stage": "준비 중", "percent": 0,
                        "speed": "", "eta": "", "created": time.time()}
    threading.Thread(target=run_job, args=(job_id, target, mode, quality), daemon=True).start()
    return jsonify(job_id=job_id)


@app.route("/progress/<job_id>")
def progress(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify(status="error", error="작업을 찾을 수 없어요. 다시 시도해 주세요."), 404
        stuck = (job.get("status") == "starting" and job.get("attempt_started")
                 and time.time() - job["attempt_started"] > 150)
        if stuck:
            job["status"] = "error"
            job["error"] = ("서버가 2분 넘게 유튜브에서 응답을 못 받고 있어요. "
                            "유튜브가 이 서버(Cloudtype)를 막고 있는 것 같아요. "
                            "Cloudtype 로그를 확인해 주세요.")
        public = {k: job.get(k) for k in
                  ("status", "stage", "percent", "speed", "eta", "title", "file_name", "error",
                   "thumbnail", "channel", "duration")}
    return jsonify(public)


@app.route("/file/<job_id>")
def file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return "파일이 아직 준비되지 않았어요.", 404

    @after_this_request
    def cleanup(response):
        with jobs_lock:
            jobs.pop(job_id, None)
        threading.Timer(60, shutil.rmtree, args=(job["out_dir"],), kwargs={"ignore_errors": True}).start()
        return response

    return send_file(job["file_path"], as_attachment=True, download_name=job["file_name"])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
