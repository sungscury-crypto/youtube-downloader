from flask import Flask, request, render_template_string
import yt_dlp

app = Flask(__name__)

html_page = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>유튜브 검색 및 다운로더</title>
</head>
<body>
    <h1 style="text-align: center;">유튜브 검색 및 다운로더</h1>
    
    <div style="text-align: center; margin-bottom: 20px;">
        <h3>1. 키워드로 검색해서 다운로드하기</h3>
        <form method="POST">
            <input type="text" name="search_query" placeholder="검색어 입력 (예: 잔잔한 팝송)" style="width: 300px;">
            <button type="submit">검색 및 다운로드</button>
        </form>
    </div>

    <div style="text-align: center;">
        <h3>2. 영상 주소로 직접 다운로드하기</h3>
        <form method="POST">
            <input type="text" name="video_url" placeholder="유튜브 주소 입력" style="width: 300px;">
            <button type="submit">다운로드 시작</button>
        </form>
    </div>
    
    <p style="text-align: center; color: red;">{{ message }}</p>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def home():
    message = ""
    if request.method == "POST":
        search_query = request.form.get("search_query")
        video_url = request.form.get("video_url")
        
        # 국가 제한을 우회하기 위한 yt-dlp 설정 추가
        ydl_opts = {
            'format': 'best',
            'geo_bypass': True,
            'geo_bypass_country': 'KR'
        }

        try:
            if search_query:
                ydl_opts['default_search'] = 'ytsearch1' # 검색 결과 첫 번째 영상 선택
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([search_query])
                message = f"'{search_query}' 검색 결과 첫 번째 영상 다운로드 완료!"
                
            elif video_url:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
                message = "다운로드 성공!"
        except Exception as e:
            message = f"오류 발생: {str(e)}"
            
    return render_template_string(html_page, message=message)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8000)
