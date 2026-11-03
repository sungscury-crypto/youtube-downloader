import os
import shutil
import tempfile

from flask import Flask, render_template, request, send_file, after_this_request
import yt_dlp

app = Flask(__name__)

# 서버에 cookies.txt가 있으면 사용 (유튜브가 서버를 '로봇'으로 막을 때 도움)
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")


def build_options(out_dir, audio_only=False):
    opts = {
        "outtmpl": os.path.join(out_dir, "%(title).80s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "restrictfilenames": False,
    }
    if audio_only:
        # 소리만 (mp3)
        opts["format"] = "ba/b"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        # 1080p 이하 가장 좋은 영상+소리 → 안 되면 가장 좋은 것 → 그래도 안 되면 아무거나
        opts["format"] = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
        opts["merge_output_format"] = "mp4"
    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    return opts


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def download():
    query = (request.form.get("query") or "").strip()
    audio_only = request.form.get("type") == "audio"

    if not query:
        return render_template("index.html", error="유튜브 주소나 검색어를 입력해 주세요.")

    # 주소가 아니면 검색어로 보고 첫 번째 영상을 받음
    target = query if query.startswith("http") else f"ytsearch1:{query}"

    out_dir = tempfile.mkdtemp()
    try:
        with yt_dlp.YoutubeDL(build_options(out_dir, audio_only)) as ydl:
            ydl.extract_info(target, download=True)

        files = [f for f in os.listdir(out_dir) if not f.endswith((".part", ".ytdl"))]
        if not files:
            raise RuntimeError("파일이 만들어지지 않았어요.")
        file_path = os.path.join(out_dir, files[0])

        @after_this_request
        def cleanup(response):
            shutil.rmtree(out_dir, ignore_errors=True)
            return response

        return send_file(file_path, as_attachment=True, download_name=files[0])

    except Exception as e:
        shutil.rmtree(out_dir, ignore_errors=True)
        msg = str(e)
        if "Sign in to confirm" in msg or "bot" in msg:
            friendly = "유튜브가 이 서버를 로봇으로 판단해 막았어요. cookies.txt를 넣거나 내 컴퓨터에서 실행해 보세요."
        elif "Requested format is not available" in msg:
            friendly = "받을 수 있는 형식이 없어요. 서버의 yt-dlp를 최신으로 다시 배포해 보세요."
        else:
            friendly = "다운로드 중 문제가 생겼어요."
        return render_template("index.html", error=f"{friendly} (자세히: {msg})")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
