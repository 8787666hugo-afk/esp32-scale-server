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

# 一律啟用；網頁上不再提供開關，保留端點只為相容既有程式。
auto_state = {
    "enabled": True,
    "changed_at": None
}

# 最近幾筆過帳結果，由 auto_loop_d.py 回報
post_log = []
MAX_LOG = 15

# 兩台無人機。看完生產工單就指派其中一台（忙碌），
# 秤上數量變動代表料被取走，該台恢復空閒。
DRONE_COUNT = 2
drones = {
    i: {
        "id": i,
        "status": "idle",     # idle｜busy
        "order": None,        # 正在處理的生產工單
        "required": 0,        # 該工單要取的數量
        "since": None,
    }
    for i in range(1, DRONE_COUNT + 1)
}

# 異動與完成通知
alerts = []
MAX_ALERTS = 12

# 多拿幾顆以上才視為異動
OVER_PICK_THRESHOLD = 2

# 存量長條的滿標值（純視覺，不代表任何庫存政策）
BAR_FULL_SCALE = 30


def now_str():
    return datetime.now().strftime("%m-%d %H:%M:%S")


def add_alert(kind, title, detail, order=None):
    """kind: over｜short｜done｜info"""
    alerts.append({
        "kind": kind,
        "title": title,
        "detail": detail,
        "order": order,
        "at": now_str(),
    })
    del alerts[:-MAX_ALERTS]
    print("通知:", kind, title, detail)


def busy_drones():
    """依指派時間由早到晚排列的忙碌無人機。"""
    return sorted(
        [d for d in drones.values() if d["status"] == "busy"],
        key=lambda d: d.get("since") or ""
    )


def release_drone(drone, taken):
    """料被取走 → 該台無人機恢復空閒，並依需求量差額發出通知。"""
    order = drone["order"]
    required = drone["required"]

    drone.update({"status": "idle", "order": None, "required": 0, "since": None})

    if required and taken > required and (taken - required) >= OVER_PICK_THRESHOLD:
        add_alert("over", f"多拿 {taken - required} 顆",
                  f"工單 {order} 需求 {required} 顆，實際取走 {taken} 顆。"
                  f"請確認是額外領用還是掉落，需另外過帳調整。", order)
    elif required and taken < required:
        add_alert("short", f"短少 {required - taken} 顆",
                  f"工單 {order} 需求 {required} 顆，實際只取走 {taken} 顆。", order)
    elif not required and taken >= OVER_PICK_THRESHOLD:
        add_alert("over", f"一次取走 {taken} 顆",
                  f"工單 {order} 未指定需求量，取走數量達 {taken} 顆，請確認。", order)
    else:
        add_alert("done", "取料完成",
                  f"工單 {order} 已取走 {taken} 顆，無人機恢復待命。", order)


def handle_count_change(previous, current):
    """秤上數量變動的處理。減少視為取料完成。"""
    taken = previous - current
    if taken <= 0:
        add_alert("info", "補料入庫",
                  f"數量由 {previous} 增加為 {current} 顆。")
        return

    busy = busy_drones()
    if busy:
        release_drone(busy[0], taken)
    else:
        add_alert("info", f"取走 {taken} 顆",
                  f"數量由 {previous} 減為 {current} 顆，但目前沒有派遣中的無人機。")


PAGE_TEMPLATE = """
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SmartBin 智慧倉儲監控</title>
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

    /* 數量為主：字級放到最大，重量不再顯示 */
    .reading { text-align: center; }
    .count-big {
      font-size: 5.2rem; font-weight: 700; line-height: 1;
      font-variant-numeric: tabular-nums; letter-spacing: -.03em;
    }
    .count-big .unit { font-size: 1.8rem; font-weight: 500; color: #7d8792; margin-left: 8px; }
    .count-sub { margin-top: 12px; }
    .pill {
      font-size: .75rem; padding: 3px 11px; border-radius: 999px;
      border: 1px solid currentColor; font-weight: 600;
    }
    .ok { color: #3fb950; }
    .bad { color: #f85149; }

    .bar { height: 6px; border-radius: 3px; background: #21262d; margin-top: 22px; overflow: hidden; }
    .bar > span { display: block; height: 100%; border-radius: 3px; transition: width .4s ease; }

    .alert {
      margin-top: 20px; padding: 12px 18px; border-radius: 10px;
      background: rgba(248,81,73,.12); border: 1px solid rgba(248,81,73,.4);
      color: #ff8a80; font-weight: 600; font-size: .95rem;
    }

    /* 無人機狀態 */
    .drones { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    @media (max-width: 520px) { .drones { grid-template-columns: 1fr; } }
    .drone {
      border-radius: 12px; padding: 18px 20px;
      border: 1px solid #2b323b; background: #12171e;
    }
    .drone.busy { border-color: #d29922; background: rgba(210,153,34,.10); }
    .drone.idle { border-color: #2d5a3a; background: rgba(63,185,80,.07); }
    .drone-head {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 10px;
    }
    .drone-name { font-size: .95rem; font-weight: 600; }
    .drone-state {
      display: inline-flex; align-items: center; gap: 7px;
      font-size: .8rem; font-weight: 700;
    }
    .drone.busy .drone-state { color: #e3b341; }
    .drone.idle .drone-state { color: #3fb950; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: currentColor; }
    .drone.busy .dot { animation: pulse 1.2s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .25; } }
    .drone-order { font-size: .82rem; color: #8b949e; line-height: 1.7; }
    .drone-order b { color: #e6edf3; font-variant-numeric: tabular-nums; }

    /* 通知 */
    .notice {
      border-radius: 12px; padding: 14px 18px; margin-bottom: 10px;
      border: 1px solid; background: #161b22;
    }
    .notice.over { border-color: #d29922; background: rgba(210,153,34,.12); }
    .notice.short { border-color: #f85149; background: rgba(248,81,73,.10); }
    .notice.done { border-color: #3fb950; background: rgba(63,185,80,.10); }
    .notice.info { border-color: #30363d; }
    .notice-head {
      display: flex; align-items: baseline; justify-content: space-between;
      gap: 12px; margin-bottom: 5px;
    }
    .notice-title { font-size: 1.02rem; font-weight: 700; }
    .notice.over .notice-title { color: #e3b341; }
    .notice.short .notice-title { color: #ff8a80; }
    .notice.done .notice-title { color: #3fb950; }
    .notice-time { font-size: .72rem; color: #6e7681; white-space: nowrap; }
    .notice-detail { font-size: .84rem; color: #a5adb8; line-height: 1.6; }


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
    <h1>SmartBin 智慧倉儲監控</h1>
    <div class="subtitle">ESP32 + HX711 ／ SAP S/4HANA 庫存整合</div>
  </header>

  <div class="card reading">
    <div class="count-big">{{ count }}<span class="unit">顆</span></div>
    <div class="count-sub">
      <span class="pill {{ 'ok' if count_ok else 'bad' }}">
        {{ '計數正常' if count_ok else '計數誤差過大' }}
      </span>
    </div>

    <div class="bar">
      <span style="width: {{ bar_pct }}%; background: {{ bar_color }};"></span>
    </div>

    {% if low_stock %}
    <div class="alert">庫存低於安全水位，請盡快補貨</div>
    {% endif %}
  </div>

  <div class="card">
    <h2>無人機狀態</h2>
    <div class="drones">
      {% for d in drone_list %}
      <div class="drone {{ d.status }}">
        <div class="drone-head">
          <span class="drone-name">無人機 {{ d.id }}</span>
          <span class="drone-state"><span class="dot"></span>{{ '忙碌' if d.status == 'busy' else '空閒' }}</span>
        </div>
        <div class="drone-order">
          {% if d.status == 'busy' %}
            處理生產工單 <b>{{ d.order }}</b><br>
            {% if d.required %}需取料 <b>{{ d.required }}</b> 顆　{% endif %}派遣於 {{ d.since }}
          {% else %}
            待命中
          {% endif %}
        </div>
      </div>
      {% endfor %}
    </div>
  </div>

  <div class="card">
    <h2>異動與完成通知</h2>
    {% if alert_list %}
      {% for a in alert_list %}
      <div class="notice {{ a.kind }}">
        <div class="notice-head">
          <span class="notice-title">{{ a.title }}</span>
          <span class="notice-time">{{ a.at }}</span>
        </div>
        <div class="notice-detail">{{ a.detail }}</div>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty">尚無通知</div>
    {% endif %}
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
    裝置 {{ device or '尚未連線' }}<br>
    最後更新 {{ received_at or '－' }}
  </footer>

</div>
</body>
</html>
"""


@app.route('/')
def index():
    count = latest_data.get("count") or 0
    pct = max(0, min(100, round(count / BAR_FULL_SCALE * 100)))
    if pct <= 10:
        color = "#f85149"
    elif pct < 66:
        color = "#d29922"
    else:
        color = "#3fb950"

    return render_template_string(
        PAGE_TEMPLATE,
        post_log=list(reversed(post_log)),
        drone_list=[drones[i] for i in sorted(drones)],
        alert_list=list(reversed(alerts)),
        bar_pct=pct,
        bar_color=color,
        **latest_data
    )


@app.route('/weight', methods=['POST'])
def receive_weight():
    global latest_data
    data = request.get_json(force=True)
    previous_count = latest_data.get("count")

    latest_data = {
        "device": data.get("device"),
        "weight_g": data.get("weight_g"),
        "unit_weight_g": data.get("unit_weight_g"),
        "count": data.get("count"),
        "count_ok": data.get("count_ok"),
        "low_stock": data.get("low_stock", False),
        "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    # 數量有變動就代表料被動過：忙碌中的無人機視為取料完成，恢復空閒
    current_count = latest_data.get("count")
    if (previous_count is not None and current_count is not None
            and current_count != previous_count):
        handle_count_change(previous_count, current_count)

    return {"status": "ok"}, 200


@app.route('/api/latest')
def api_latest():
    return jsonify(latest_data)


@app.route('/api/drones', methods=['GET'])
def api_drones():
    return jsonify(list(drones.values()))


@app.route('/api/drone/assign', methods=['POST'])
def api_drone_assign():
    """看完生產工單後指派無人機 → 該台變忙碌。

    body: {"order": "2001016", "required": 5, "drone": 1}
    drone 省略時自動挑一台空閒的。
    """
    body = request.get_json(force=True, silent=True) or {}
    order = str(body.get("order", "")).strip()
    if not order:
        return jsonify({"error": "缺少 order"}), 400
    required = int(body.get("required", 0) or 0)

    wanted = body.get("drone")
    if wanted is not None:
        target = drones.get(int(wanted))
        if target is None:
            return jsonify({"error": "無人機編號不存在"}), 400
    else:
        target = next((d for d in drones.values() if d["status"] == "idle"), None)
        if target is None:
            return jsonify({"error": "沒有空閒的無人機", "drones":
                            list(drones.values())}), 409

    target.update({
        "status": "busy",
        "order": order,
        "required": required,
        "since": now_str(),
    })
    add_alert("info", f"派遣無人機 {target['id']}",
              f"開始處理生產工單 {order}"
              + (f"，需取料 {required} 顆" if required else ""), order)
    return jsonify(target), 200


@app.route('/api/drone/complete', methods=['POST'])
def api_drone_complete():
    """工單處理完成（例如 SAP 過帳成功）→ 發出完成通知並釋放無人機。

    body: {"order": "2001016", "message": "Material document 49... posted",
           "ok": true, "drone": 1}
    """
    body = request.get_json(force=True, silent=True) or {}
    order = str(body.get("order", "")).strip()
    message = str(body.get("message", ""))
    ok = bool(body.get("ok", True))

    wanted = body.get("drone")
    if wanted is not None:
        target = drones.get(int(wanted))
    else:
        target = next((d for d in drones.values()
                       if d["status"] == "busy" and d["order"] == order), None)

    if target is not None:
        target.update({"status": "idle", "order": None,
                       "required": 0, "since": None})

    if ok:
        add_alert("done", f"工單 {order} 完成", message or "已完成處理", order)
    else:
        add_alert("short", f"工單 {order} 未完成", message or "處理失敗", order)

    return jsonify(target or {}), 200


@app.route('/api/drone/reset', methods=['POST'])
def api_drone_reset():
    """把所有無人機恢復空閒並清空通知（展示前重置用）。"""
    for d in drones.values():
        d.update({"status": "idle", "order": None, "required": 0, "since": None})
    alerts.clear()
    return {"status": "ok"}, 200


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

        if from_form is not None:  # 由網頁按鈕送出，導回首頁
            return ('', 303, {'Location': '/'})
    return jsonify(auto_state)


@app.route('/api/log', methods=['GET', 'POST'])
def api_log():
    """本機過帳程式回報結果。"""
    if request.method == 'POST':
        body = request.get_json(force=True, silent=True) or {}
        entry = {
            "at": now_str(),
            "movement": str(body.get("movement", "")),
            "message": str(body.get("message", "")),
            "ok": bool(body.get("ok", False)),
        }
        post_log.append(entry)
        del post_log[:-MAX_LOG]
        return {"status": "ok"}, 200
    return jsonify(post_log)


@app.route('/api/alerts', methods=['GET', 'POST'])
def api_alerts():
    """通知清單。POST 可手動加一則（測試用）。"""
    if request.method == 'POST':
        body = request.get_json(force=True, silent=True) or {}
        add_alert(str(body.get("kind", "info")),
                  str(body.get("title", "")),
                  str(body.get("detail", "")),
                  body.get("order"))
        return {"status": "ok"}, 200
    return jsonify(alerts)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
