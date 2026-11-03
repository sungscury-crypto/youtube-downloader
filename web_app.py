import os
from flask import Flask, request, render_template_string, send_file
import yt_dlp

app = Flask(__name__)

html_page = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>유튜브 다운로더</title></head>
<body>
    <h1 style="text-align: center;">유튜브 다운로더</h1>
    <div style="text-align: center;">
        <form method="POST">
            <input type="text" name="query" placeholder="유튜브 주소 또는 검색어" style="width: 300px; padding: 5px;">
            <button type="submit" style="padding: 5px 10px;">다운로드 시작</button>
        </form>
        <p style="color: red;">{{ message }}</p>
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def home():
    message = ""
    if request.method == "POST":
        query = request.form.get("query")
        ydl_opts = {
            'format': '22/18/b',
            'outtmpl': 'video_%(id)s.%(ext)s',
            'geo_bypass': True,
            'geo_bypass_country': 'KR',
            'noplaylist': True
        }
        if not query.startswith("http"):
            ydl_opts['default_search'] = 'ytsearch1'
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=True)
                filename = ydl.prepare_filename(info)
            return send_file(filename, as_attachment=True)
        except Exception as e:
            message = f"오류 발생: {str(e)}"
    return render_template_string(html_page, message=message)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8000)
