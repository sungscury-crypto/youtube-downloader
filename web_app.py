import yt_dlp
from flask import Flask, request, render_template_string

app = Flask(__name__)

html_page = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>유튜브 다운로더</title>
    <style>
        body { font-family: sans-serif; text-align: center; margin-top: 100px; }
        input { width: 50%; padding: 12px; font-size: 16px; border: 1px solid #ccc; border-radius: 4px; }
        button { padding: 12px 24px; font-size: 16px; background-color: #ff0000; color: white; border: none; border-radius: 4px; cursor: pointer; margin-left: 10px; }
        button:hover { background-color: #cc0000; }
        .msg { margin-top: 30px; font-size: 18px; color: #0066cc; font-weight: bold; }
    </style>
</head>
<body>
    <h1>유튜브 영상 다운로더</h1>
    <form method="POST">
        <input type="text" name="url" placeholder="여기에 유튜브 영상 주소를 붙여넣으세요" required>
        <button type="submit">다운로드 시작</button>
    </form>
    {% if message %}
    <div class="msg">{{ message }}</div>
    {% endif %}
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def home():
    message = ""
    if request.method == "POST":
        url = request.form["url"]
        ydl_opts = {
            'outtmpl': './%(title)s.%(ext)s',
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            message = "✅ 영상 다운로드가 완료되었습니다! 바탕화면 폴더를 확인하세요."
        except Exception as e:
            message = f"❌ 다운로드 중 오류가 발생했습니다: {e}"
            
    return render_template_string(html_page, message=message)

if __name__ == "__main__":
    app.run(port=5001)
