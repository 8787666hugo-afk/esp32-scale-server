from datetime import datetime

from flask import Flask, jsonify, render_template_string, request

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

# 全自動過帳開關。由網頁切換，本機的 auto_runner.py 會定期讀取。
auto_state = {
    "enabled": False,
    "changed_at": None
}

# 最近幾筆過帳結果，由 auto_runner.py 回報
post_log = []
MAX_LOG = 15

# 領料派遣通知，由 dispatch_watch.py 回報；以單據號碼為鍵
dispatches = {}

PAGE_TEMPLATE = """
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SmartBin 智慧秤重監控</title>
  <meta http-equiv="refresh" content="5">
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 32px 16px 64px;
      font-family: "Segoe UI", "Microsoft JhengHei", system-ui, sans-serif;
      background: radial-gradient(ellipse at top, #16202e 0%, #0d1117 55%);
      color: #e6edf3; min-height: 100vh;
    }
    .wrap { max-width: 720px; margin: 0 auto; }

    header { text-align: center; margin-bottom: 28px; }
    h1 { margin: 0 0 6px; font-size: 1.5rem; font-weight: 600; letter-spacing: .04em; }
    .subtitle { color: #7d8792; font-size: .85rem; }

    .card {
      background: #161b22; border: 1px solid #262d36; border-radius: 14px;
      padding: 26px; margin-bottom: 18px;
    }

    .reading { text-align: center; }
    .weight {
      font-size: 4.2rem; font-weight: 700; line-height: 1.05;
      font-variant-numeric: tabular-nums; letter-spacing: -.02em;
    }
    .weight .unit { font-size: 1.6rem; font-weight: 500; color: #7d8792; margin-left: 6px; }
    .count-row {
      margin-top: 14px; display: flex; justify-content: center;
      align-items: baseline; gap: 10px; flex-wrap: wrap;
    }
    .count { font-size: 1.9rem; font-weight: 600; font-variant-numeric: tabular-nums; }
    .count .unit { font-size: 1rem; color: #7d8792; margin-left: 4px; }
    .pill {
      font-size: .75rem; padding: 3px 11px; border-radius: 999px;
      border: 1px solid currentColor; font-weight: 600;
    }
    .ok { color: #3fb950; }
    .bad { color: #f85149; }

    .bar { height: 6px; border-radius: 3px; background: #21262d; margin-top: 22px; overflow: hidden; }
    .bar > span { display: block; height: 100%; border-radius: 3px; transition: width .4s ease; }
    .bar-labels {
      display: flex; justify-content: space-between;
      font-size: .72rem; color: #6e7681; margin-top: 6px;
    }

    .alert {
      margin-top: 20px; padding: 12px 18px; border-radius: 10px;
      background: rgba(248,81,73,.12); border: 1px solid rgba(248,81,73,.4);
      color: #ff8a80; font-weight: 600; font-size: .95rem;
    }

    .notice {
      border-radius: 12px; padding: 16px 20px; margin-bottom: 12px;
      border: 1px solid; background: #161b22;
    }
    .notice.dispatched { border-color: #388bfd; background: rgba(56,139,253,.10); }
    .notice.insufficient { border-color: #f85149; background: rgba(248,81,73,.10); }
    .notice.picked { border-color: #3fb950; background: rgba(63,185,80,.10); }
    .notice-head {
      display: flex; align-items: center; gap: 10px;
      font-size: .74rem; color: #8b949e; margin-bottom: 8px;
    }
    .notice-tag {
      padding: 2px 9px; border-radius: 999px;
      background: rgba(255,255,255,.07); font-weight: 600;
    }
    .notice-doc { font-variant-numeric: tabular-nums; }
    .diff {
      margin-top: 12px; padding: 10px 14px; border-radius: 8px;
      font-size: .9rem; font-weight: 600;
    }
    .diff.over { background: rgba(210,153,34,.14); color: #e3b341;
                 border: 1px solid rgba(210,153,34,.45); }
    .diff.under { background: rgba(248,81,73,.12); color: #ff8a80;
                  border: 1px solid rgba(248,81,73,.4); }
    .diff-hint { font-weight: 400; font-size: .8rem; opacity: .8; margin-top: 4px; }

    .notice-main { font-size: 1.15rem; font-weight: 600; }
    .notice-main b { font-size: 1.4rem; }
    .notice-sub { font-size: .82rem; color: #8b949e; margin-top: 5px; }

    .auto-head {
      display: flex; align-items: center; justify-content: space-between;
      gap: 16px; flex-wrap: wrap;
    }
    .auto-label { font-size: 1rem; font-weight: 600; }
    .auto-hint { color: #7d8792; font-size: .8rem; margin-top: 4px; }
    .status {
      display: inline-flex; align-items: center; gap: 7px;
      font-size: .85rem; font-weight: 600;
    }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: #6e7681; }
    .dot.live { background: #3fb950; box-shadow: 0 0 0 4px rgba(63,185,80,.16); }

    button {
      font: inherit; font-size: .95rem; font-weight: 600;
      padding: 11px 26px; border-radius: 9px; border: 1px solid transparent;
      cursor: pointer; transition: filter .15s ease;
    }
    button:hover { filter: brightness(1.12); }
    .btn-start { background: #238636; color: #fff; }
    .btn-stop { background: transparent; color: #c9d1d9; border-color: #3d444d; }

    h2 { font-size: .82rem; font-weight: 600; color: #7d8792;
         text-transform: uppercase; letter-spacing: .08em; margin: 0 0 14px; }
    table { width: 100%; border-collapse: collapse; font-size: .84rem; }
    th { text-align: left; color: #6e7681; font-weight: 500; padding-bottom: 8px; }
    td { padding: 9px 0; border-top: 1px solid #21262d; vertical-align: top; }
    td.time { color: #6e7681; white-space: nowrap; width: 96px; font-variant-numeric: tabular-nums; }
    td.result { text-align: right; white-space: nowrap; }
    .empty { color: #6e7681; font-size: .85rem; text-align: center; padding: 8px 0; }

    footer { text-align: center; color: #565f6a; font-size: .76rem; margin-top: 24px; line-height: 1.7; }
  </style>
</head>
<body>
<div class="wrap">

  <header>
    <h1>SmartBin 智慧秤重監控</h1>
    <div class="subtitle">ESP32 + HX711 ／ SAP S/4HANA 庫存整合</div>
  </header>

  {% if dispatch_list %}
  {% for d in dispatch_list %}
  <div class="notice {{ d.status }}">
    <div class="notice-head">
      <span class="notice-tag">{{ d.element }}</span>
      <span class="notice-doc">{{ d.document }}</span>
    </div>
    {% if d.status == 'dispatched' %}
      <div class="notice-main">派遣無人機取料 <b>{{ d.required }}</b> 顆</div>
      <div class="notice-sub">派遣時秤上 {{ d.scale_count }} 顆，庫存充足</div>
    {% elif d.status == 'insufficient' %}
      <div class="notice-main">庫存不足，無法派遣</div>
      <div class="notice-sub">需求 {{ d.required }} 顆，秤上僅 {{ d.scale_count }} 顆</div>
    {% elif d.status == 'picked' %}
      <div class="notice-main">取料完成</div>
      <div class="notice-sub">已取 {{ d.taken }} 顆（需求 {{ d.required }} 顆）</div>
    {% elif d.status in ('posted', 'checked') %}
      <div class="notice-main">
        {{ 'SAP 已過帳' if d.status == 'posted' else 'SAP 檢查通過' }}
      </div>
      <div class="notice-sub">{{ d.message }}</div>
    {% elif d.status == 'post_failed' %}
      <div class="notice-main">SAP 過帳失敗</div>
      <div class="notice-sub">{{ d.message }}</div>
    {% endif %}

    {% if d.taken is defined and d.taken > d.required %}
      <div class="diff over">
        ⚠ 多拿 {{ d.taken - d.required }} 顆（需求 {{ d.required }}，實際取走 {{ d.taken }}）
        <div class="diff-hint">請確認是額外領用還是掉落，需另外過帳調整</div>
      </div>
    {% elif d.taken is defined and d.taken < d.required %}
      <div class="diff under">
        ⚠ 短少 {{ d.required - d.taken }} 顆（需求 {{ d.required }}，實際取走 {{ d.taken }}）
      </div>
    {% endif %}
  </div>
  {% endfor %}
  {% endif %}

  <div class="card reading">
    <div class="weight">{{ '%.1f'|format(weight_g or 0) }}<span class="unit">g</span></div>
    <div class="count-row">
      <div class="count">{{ count }}<span class="unit">顆</span></div>
      <span class="pill {{ 'ok' if count_ok else 'bad' }}">
        {{ '誤差正常' if count_ok else '誤差過大' }}
      </span>
    </div>

    <div class="bar">
      <span style="width: {{ bar_pct }}%; background: {{ bar_color }};"></span>
    </div>
    <div class="bar-labels">
      <span>0</span>
      <span>安全庫存 {{ safety_stock }}　｜　再訂購點 {{ reorder_point }}</span>
    </div>

    {% if low_stock %}
    <div class="alert">庫存低於安全水位，請盡快補貨</div>
    {% endif %}
  </div>

  <div class="card">
    <div class="auto-head">
      <div>
        <div class="auto-label">SAP 全自動過帳</div>
        <div class="auto-hint">
          {% if auto_enabled %}
            數量變動時自動建立物料憑證，請保持 SAP GUI 登入
          {% else %}
            目前不會自動過帳
          {% endif %}
        </div>
      </div>
      <div class="status">
        <span class="dot {{ 'live' if auto_enabled else '' }}"></span>
        {{ '運作中' if auto_enabled else '已停用' }}
      </div>
    </div>
    <form method="post" action="/api/auto" style="margin-top:18px;">
      <input type="hidden" name="enabled" value="{{ '0' if auto_enabled else '1' }}">
      <button type="submit" class="{{ 'btn-stop' if auto_enabled else 'btn-start' }}">
        {{ '停用自動過帳' if auto_enabled else '啟動自動過帳' }}
      </button>
    </form>
  </div>

  <div class="card">
    <h2>過帳紀錄</h2>
    {% if post_log %}
    <table>
      <tr><th>時間</th><th>異動</th><th style="text-align:right;">結果</th></tr>
      {% for row in post_log %}
      <tr>
        <td class="time">{{ row.at }}</td>
        <td>{{ row.movement }}</td>
        <td class="result {{ 'ok' if row.ok else 'bad' }}">{{ row.message }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">尚無紀錄</div>
    {% endif %}
  </div>

  <footer>
    裝置 {{ device or '尚未連線' }}　｜　單重 {{ '%.1f'|format(unit_weight_g or 0) }} g<br>
    最後更新 {{ received_at or '－' }}
  </footer>

</div>
</body>
</html>
"""

# 與 SAP 物料主檔 MAG-001 一致，用於網頁上的庫存水位顯示
SAFETY_STOCK = 3
REORDER_POINT = 20
BAR_FULL_SCALE = 30


@app.route('/')
def index():
    count = latest_data.get("count") or 0
    pct = max(0, min(100, round(count / BAR_FULL_SCALE * 100)))
    if count <= SAFETY_STOCK:
        color = "#f85149"
    elif count < REORDER_POINT:
        color = "#d29922"
    else:
        color = "#3fb950"
    # 派遣通知：未完成的排前面，其次依時間新到舊
    order = {"insufficient": 0, "dispatched": 1, "picked": 2}
    dispatch_list = sorted(
        dispatches.values(),
        key=lambda d: (order.get(d.get("status"), 9), d.get("at", "")),
        reverse=False,
    )

    return render_template_string(
        PAGE_TEMPLATE,
        auto_enabled=auto_state["enabled"],
        post_log=list(reversed(post_log)),
        dispatch_list=dispatch_list,
        bar_pct=pct,
        bar_color=color,
        safety_stock=SAFETY_STOCK,
        reorder_point=REORDER_POINT,
        **latest_data
    )


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


@app.route('/api/auto', methods=['GET', 'POST'])
def api_auto():
    """讀取或切換全自動過帳開關。

    GET  → 回傳目前狀態
    POST → 以表單 enabled=1/0 或 JSON {"enabled": true} 切換
    """
    if request.method == 'POST':
        from_form = request.form.get('enabled')
        if from_form is not None:
            value = from_form in ('1', 'true', 'on')
        else:
            body = request.get_json(silent=True) or {}
            if 'enabled' not in body:
                return jsonify({"error": "缺少 enabled 參數"}), 400
            value = bool(body['enabled'])

        auto_state["enabled"] = value
        auto_state["changed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("自動過帳開關 →", "ON" if value else "OFF")

        if from_form is not None:  # 由網頁按鈕送出，導回首頁
            return ('', 303, {'Location': '/'})
    return jsonify(auto_state)


@app.route('/api/log', methods=['GET', 'POST'])
def api_log():
    """本機過帳程式回報結果。"""
    if request.method == 'POST':
        body = request.get_json(force=True, silent=True) or {}
        entry = {
            "at": datetime.now().strftime("%m-%d %H:%M:%S"),
            "movement": str(body.get("movement", "")),
            "message": str(body.get("message", "")),
            "ok": bool(body.get("ok", False)),
        }
        post_log.append(entry)
        del post_log[:-MAX_LOG]
        print("過帳回報:", entry)
        return {"status": "ok"}, 200
    return jsonify(post_log)


@app.route('/api/dispatch', methods=['GET', 'POST'])
def api_dispatch():
    """領料派遣通知。

    POST → 由 dispatch_watch.py 回報一筆需求的派遣／完成狀態
    GET  → 回傳目前所有派遣通知
    """
    if request.method == 'POST':
        body = request.get_json(force=True, silent=True) or {}
        document = str(body.get("document", "")).strip()
        if not document:
            return jsonify({"error": "缺少 document"}), 400

        entry = dispatches.get(document, {})
        entry.update({
            "document": document,
            "element": str(body.get("element", "")),
            "required": int(body.get("required", 0) or 0),
            "scale_count": int(body.get("scale_count", 0) or 0),
            "status": str(body.get("status", "dispatched")),
            "at": datetime.now().strftime("%m-%d %H:%M:%S"),
        })
        if "taken" in body:
            entry["taken"] = int(body.get("taken") or 0)
        if "message" in body:
            entry["message"] = str(body.get("message") or "")
        dispatches[document] = entry
        print("派遣通知:", entry)
        return {"status": "ok"}, 200

    return jsonify(list(dispatches.values()))


@app.route('/api/dispatch/clear', methods=['POST'])
def api_dispatch_clear():
    """清除所有派遣通知。"""
    dispatches.clear()
    return {"status": "ok"}, 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
