# gunicorn 設定。gunicorn 啟動時會自動讀工作目錄下的這個檔，
# 所以 Render 的 Start Command 維持 `gunicorn forecast_api:app` 就好。
#
# 為什麼需要這個檔：
#   /forecast/analyze 要等 Gemini 讀完 43 個月歷史 + 事件清單再推論，
#   實測要 30 秒以上。gunicorn 預設 timeout 就是 30 秒，worker 會被
#   直接砍掉，Flask 的 errorhandler 根本沒機會執行，ABAP 只收得到
#   一坨 HTML 的 500，看不出原因。
#
#   小筆測試資料在 30 秒內跑得完，所以這個 bug 只有在送真實的
#   43 個月資料時才會出現 —— 這就是交接文件裡那個「重跑就好、
#   根因未確認」的 500。
timeout = 300

# 磅秤看板的輪詢和預測分析共用同一個 app。只有一個 worker 的話，
# 一筆分析跑五分鐘會把看板整個卡住。
workers = 2

# 冷啟動後第一筆請求含 import 與模型初始化，給寬一點
graceful_timeout = 60
keepalive = 5
