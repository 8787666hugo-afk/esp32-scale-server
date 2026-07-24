from flask import Flask, request, jsonify, render_template_string
from datetime import datetime

app = Flask(__name__)

latest_data = {
    "device": None,
    "weight_g": 0.0,
    "unit_weight_g": 0.0,
    "count": 0,
    "count_ok": True,
    "low_stock": False,
    "received_at": None
}

PAGE_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>即時重量監控</title>
  <meta http-equiv="refresh" content="3">
  <style>
    body { font-family: sans-serif; text-align: center; margin-top: 60px; background:#111; color:#eee; }
    .weight { font-size: 4rem; font-weight: bold; }
    .count { font-size: 2rem; margin-top: 10px; }
    .status-ok { color: #4caf50; }
    .status-bad { color: #f44336; }
    .meta { margin-top: 30px; color: #888; font-size: 0.9rem; }
    .warning {
      margin-top: 30px;
      display: inline-block;
      padding: 12px 24px;
      border-radius: 8px;
      background: #f44336;
      color: #fff;
      font-size: 1.3rem;
      font-weight: bold;
    }
  </style>
</head>
<body>
  <h1>即時重量監控</h1>
  <div class="weight">{{ weight_g }} g</div>
  <div class="count">
    數量: {{ count }} 顆
    <span class="{{ 'status-ok' if count_ok else 'status-bad' }}">
      {{ '(正常)' if count_ok else '(誤差過大)' }}
    </span>
  </div>
  {% if low_stock %}
  <div class="warning">⚠ 安全庫存警告：數量過低，請盡快補貨！</div>
  {% endif %}
  <div class="meta">裝置: {{ device or '尚未收到資料' }} ｜ 最後更新: {{ received_at or '-' }}</div>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(PAGE_TEMPLATE, **latest_data)


@app.route('/weight', methods=['POST'])
def receive_weight():
    global latest_data
    data = request.get_json(force=True)
    latest_data = {
        "device": data.get("device"),
        "weight_g": data.get("weight_g"),
        "unit_weight_g": data.get("unit_weight_g"),
        "count": data.get("count"),
        "count_ok": data.get("count_ok"),
        "low_stock": data.get("low_stock", False),
        "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    print("收到資料:", latest_data)
    return {"status": "ok"}, 200


@app.route('/api/latest')
def api_latest():
    return jsonify(latest_data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
