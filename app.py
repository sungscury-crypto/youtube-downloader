import os
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


def build_options(job_id, out_dir, audio_only):
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
        "quiet": True,
        "retries": 10,
        "fragment_retries": 10,
        "http_chunk_size": 10 * 1024 * 1024,  # 10MB씩 나눠 받기 (빈 파일·속도 제한 줄임)
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
    }
    if audio_only:
        opts["format"] = "ba/b"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        opts["format"] = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
        opts["merge_output_format"] = "mp4"
    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    return opts


def friendly_error(msg):
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


def run_job(job_id, target, audio_only):
    out_dir = tempfile.mkdtemp()
    update(job_id, out_dir=out_dir)
    last_error = ""
    for i, attempt in enumerate(ATTEMPTS, start=1):
        # 이전 시도에서 남은 조각 파일 지우기
        for f in os.listdir(out_dir):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
        if i > 1:
            update(job_id, status="starting", percent=0, speed="", eta="",
                   stage=f"다른 방법으로 다시 시도 중 ({i}/{len(ATTEMPTS)}: {attempt['name']})")
        else:
            update(job_id, stage="영상 정보 확인 중… (30초~1분)")

        opts = build_options(job_id, out_dir, audio_only)
        if attempt["clients"]:
            opts["extractor_args"] = {"youtube": {"player_client": attempt["clients"]}}
        if attempt["format"] and not audio_only:
            opts["format"] = attempt["format"]
            opts.pop("merge_output_format", None)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=True)
                if info and "entries" in info:  # 검색어로 받은 경우
                    info = info["entries"][0]
                update(job_id, title=(info or {}).get("title", ""))

            files = [f for f in os.listdir(out_dir)
                     if not f.endswith((".part", ".ytdl"))
                     and os.path.getsize(os.path.join(out_dir, f)) > 0]
            if not files:
                raise RuntimeError("The downloaded file is empty")
            update(job_id, status="done", stage="완료!", percent=100,
                   file_path=os.path.join(out_dir, files[0]), file_name=files[0])
            return
        except Exception as e:
            last_error = str(e)
            print(f"[{job_id}] 시도 {i} ({attempt['name']}) 실패: {last_error}", flush=True)
            # 주소가 틀렸거나 비공개 영상이면 다른 방법도 소용없음
            if any(k in last_error for k in ("Private video", "Video unavailable", "is not a valid URL")):
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
    audio_only = data.get("type") == "audio"
    if not query:
        return jsonify(error="유튜브 주소나 검색어를 입력해 주세요."), 400

    target = query if query.startswith("http") else f"ytsearch1:{query}"
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"status": "starting", "stage": "준비 중", "percent": 0,
                        "speed": "", "eta": "", "created": time.time()}
    threading.Thread(target=run_job, args=(job_id, target, audio_only), daemon=True).start()
    return jsonify(job_id=job_id)


@app.route("/progress/<job_id>")
def progress(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify(status="error", error="작업을 찾을 수 없어요. 다시 시도해 주세요."), 404
        public = {k: job.get(k) for k in
                  ("status", "stage", "percent", "speed", "eta", "title", "file_name", "error")}
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
