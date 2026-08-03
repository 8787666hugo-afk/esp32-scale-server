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


PAGE_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SmartBin 智慧倉儲監控</title>
  <meta http-equiv="refresh" content="5">
  <style>
    /* 莫蘭迪色系：低飽和、帶灰調，避免高對比造成的疲勞 */
    :root {
      --bg:        #ece8e1;   /* 米灰背景 */
      --surface:   #f7f5f1;   /* 卡片 */
      --line:      #ddd6cc;   /* 分隔線 */
      --ink:       #4a4540;   /* 主文字 */
      --ink-soft:  #8c8378;   /* 次要文字 */
      --sage:      #8fa08c;   /* 靜置／正常 */
      --sage-bg:   #e4e9e1;
      --clay:      #c2a07d;   /* 忙碌／注意 */
      --clay-bg:   #f0e6d9;
      --rose:      #b98a86;   /* 異常 */
      --rose-bg:   #f2e3e1;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 34px 16px 64px;
      font-family: "Segoe UI", "Microsoft JhengHei", system-ui, sans-serif;
      color: var(--ink); min-height: 100vh;
      position: relative; overflow-x: hidden;
      /* 兩團柔霧 + 細點陣，讓大片留白有層次又不搶內容 */
      background:
        radial-gradient(620px circle at 12% 8%,  rgba(143,160,140,.20), transparent 62%),
        radial-gradient(560px circle at 88% 82%, rgba(194,160,125,.20), transparent 62%),
        radial-gradient(rgba(140,131,120,.13) 1px, transparent 1px) 0 0 / 26px 26px,
        var(--bg);
    }

    /* 裝飾層：不吃滑鼠事件，純視覺 */
    .decor {
      position: fixed; inset: 0; z-index: 0;
      pointer-events: none; overflow: hidden;
    }
    .decor svg { position: absolute; }
    .decor .shelf-l { left: 0;  bottom: 0; width: 260px; opacity: .5; }
    .decor .shelf-r { right: 0; bottom: 0; width: 240px; opacity: .5; }
    .decor .fly-1 { left: 6%;  top: 22%; width: 74px; opacity: .42;
                    animation: drift 13s ease-in-out infinite; }
    .decor .fly-2 { right: 7%; top: 14%; width: 58px; opacity: .34;
                    animation: drift 17s ease-in-out infinite reverse; }
    .decor .fly-3 { right: 11%; top: 52%; width: 46px; opacity: .26;
                    animation: drift 21s ease-in-out infinite; }
    @keyframes drift {
      0%,100% { transform: translate(0,0) rotate(-2deg); }
      50%     { transform: translate(10px,-18px) rotate(2deg); }
    }
    /* 螢幕不夠寬時裝飾會壓到內容，直接收起來 */
    @media (max-width: 1180px) { .decor { display: none; } }

    .wrap { max-width: 720px; margin: 0 auto; position: relative; z-index: 1; }

    header { text-align: center; margin-bottom: 26px; }
    h1 { margin: 0 0 6px; font-size: 1.45rem; font-weight: 600; letter-spacing: .04em; }
    .subtitle { color: var(--ink-soft); font-size: .84rem; }

    .card {
      background: var(--surface); border: 1px solid var(--line);
      border-radius: 16px; padding: 26px; margin-bottom: 16px;
      box-shadow: 0 1px 2px rgba(74,69,64,.04);
    }

    /* 數量為主 */
    .reading { text-align: center; }
    .count-big {
      font-size: 5rem; font-weight: 700; line-height: 1;
      font-variant-numeric: tabular-nums; letter-spacing: -.03em;
    }
    .count-big .unit {
      font-size: 1.7rem; font-weight: 500; color: var(--ink-soft); margin-left: 8px;
    }
    .count-sub { margin-top: 12px; }
    .pill {
      font-size: .74rem; padding: 4px 13px; border-radius: 999px;
      font-weight: 600; border: 1px solid transparent;
    }
    .pill.ok  { color: #5f7a5c; background: var(--sage-bg); border-color: #c9d4c5; }
    .pill.bad { color: #96605b; background: var(--rose-bg); border-color: #e0c4c1; }

    .bar {
      height: 7px; border-radius: 4px; background: #e2ddd4;
      margin-top: 22px; overflow: hidden;
    }
    .bar > span { display: block; height: 100%; border-radius: 4px; transition: width .4s ease; }

    .alert {
      margin-top: 20px; padding: 12px 18px; border-radius: 11px;
      background: var(--rose-bg); border: 1px solid #e0c4c1;
      color: #96605b; font-weight: 600; font-size: .92rem;
    }

    /* 無人機 */
    .drones { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    @media (max-width: 520px) { .drones { grid-template-columns: 1fr; } }
    .drone {
      display: flex; gap: 15px; align-items: flex-start;
      border-radius: 14px; padding: 17px 18px; border: 1px solid var(--line);
    }
    .drone.idle { background: var(--sage-bg); border-color: #c9d4c5; }
    .drone.busy { background: var(--clay-bg); border-color: #e0cdb2; }

    .drone-icon { flex: 0 0 46px; width: 46px; height: 46px; }
    .drone.busy .rotor { animation: spin .9s linear infinite; transform-origin: center; }
    .drone.busy .beam  { animation: beam 1.6s ease-in-out infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes beam { 0%,100% { opacity: .15; } 50% { opacity: .55; } }

    .drone-body { min-width: 0; }
    .drone-head {
      display: flex; align-items: baseline; gap: 9px; margin-bottom: 5px; flex-wrap: wrap;
    }
    .drone-name { font-size: .94rem; font-weight: 600; }
    .drone-state { font-size: .76rem; font-weight: 700; letter-spacing: .03em; }
    .drone.idle .drone-state { color: #5f7a5c; }
    .drone.busy .drone-state { color: #97744a; }
    .drone-order { font-size: .8rem; color: var(--ink-soft); line-height: 1.65; }
    .drone-order b { color: var(--ink); font-variant-numeric: tabular-nums; }

    /* 通知 */
    .notice {
      border-radius: 12px; padding: 13px 17px; margin-bottom: 9px;
      border: 1px solid var(--line); background: #fbfaf7;
    }
    .notice.over  { border-color: #e0cdb2; background: var(--clay-bg); }
    .notice.short { border-color: #e0c4c1; background: var(--rose-bg); }
    .notice.done  { border-color: #c9d4c5; background: var(--sage-bg); }
    .notice-head {
      display: flex; align-items: baseline; justify-content: space-between;
      gap: 12px; margin-bottom: 4px;
    }
    .notice-title { font-size: .98rem; font-weight: 700; }
    .notice.over  .notice-title { color: #97744a; }
    .notice.short .notice-title { color: #96605b; }
    .notice.done  .notice-title { color: #5f7a5c; }
    .notice-time { font-size: .71rem; color: var(--ink-soft); white-space: nowrap; }
    .notice-detail { font-size: .83rem; color: var(--ink-soft); line-height: 1.6; }

    h2 { font-size: .78rem; font-weight: 600; color: var(--ink-soft);
         text-transform: uppercase; letter-spacing: .1em; margin: 0 0 14px; }
    table { width: 100%; border-collapse: collapse; font-size: .83rem; }
    th { text-align: left; color: var(--ink-soft); font-weight: 500; padding-bottom: 8px; }
    td { padding: 9px 0; border-top: 1px solid var(--line); vertical-align: top; }
    td.time { color: var(--ink-soft); white-space: nowrap; width: 96px;
              font-variant-numeric: tabular-nums; }
    td.result { text-align: right; white-space: nowrap; }
    td.result.ok  { color: #5f7a5c; }
    td.result.bad { color: #96605b; }
    .empty { color: var(--ink-soft); font-size: .84rem; text-align: center; padding: 8px 0; }

    footer { text-align: center; color: var(--ink-soft); font-size: .75rem;
             margin-top: 22px; line-height: 1.7; opacity: .85; }
  </style>
</head>
<body>
<div class="decor" aria-hidden="true">

  <!-- 左下：倉儲料架 -->
  <svg class="shelf-l" viewBox="0 0 260 200" fill="none">
    <g stroke="#a99e90" stroke-width="2.4" stroke-linecap="round">
      <line x1="30" y1="24" x2="30" y2="196"/>
      <line x1="230" y1="24" x2="230" y2="196"/>
      <line x1="24" y1="24"  x2="236" y2="24"/>
      <line x1="24" y1="82"  x2="236" y2="82"/>
      <line x1="24" y1="140" x2="236" y2="140"/>
      <line x1="24" y1="196" x2="236" y2="196"/>
    </g>
    <g fill="#c2a07d" opacity=".55">
      <rect x="44"  y="46" width="42" height="34" rx="3"/>
      <rect x="98"  y="54" width="34" height="26" rx="3"/>
      <rect x="160" y="42" width="48" height="38" rx="3"/>
      <rect x="52"  y="108" width="36" height="30" rx="3"/>
      <rect x="120" y="100" width="46" height="38" rx="3"/>
      <rect x="180" y="112" width="30" height="26" rx="3"/>
      <rect x="46"  y="160" width="50" height="34" rx="3"/>
      <rect x="140" y="166" width="40" height="28" rx="3"/>
    </g>
    <g stroke="#8fa08c" stroke-width="1.6" opacity=".7">
      <line x1="44"  y1="63"  x2="86"  y2="63"/>
      <line x1="160" y1="61"  x2="208" y2="61"/>
      <line x1="120" y1="119" x2="166" y2="119"/>
      <line x1="46"  y1="177" x2="96"  y2="177"/>
    </g>
  </svg>

  <!-- 右下：棧板與箱子 -->
  <svg class="shelf-r" viewBox="0 0 240 170" fill="none">
    <g fill="#c2a07d" opacity=".5">
      <rect x="70"  y="52"  width="54" height="44" rx="3"/>
      <rect x="132" y="66"  width="40" height="30" rx="3"/>
      <rect x="58"  y="104" width="62" height="34" rx="3"/>
      <rect x="128" y="104" width="52" height="34" rx="3"/>
    </g>
    <g stroke="#8fa08c" stroke-width="1.6" opacity=".7">
      <line x1="70" y1="72"  x2="124" y2="72"/>
      <line x1="58" y1="120" x2="120" y2="120"/>
      <line x1="128" y1="120" x2="180" y2="120"/>
    </g>
    <g stroke="#a99e90" stroke-width="3" stroke-linecap="round">
      <line x1="46" y1="146" x2="196" y2="146"/>
      <line x1="46" y1="156" x2="196" y2="156"/>
      <line x1="58" y1="146" x2="58" y2="156"/>
      <line x1="120" y1="146" x2="120" y2="156"/>
      <line x1="184" y1="146" x2="184" y2="156"/>
    </g>
  </svg>

  <!-- 飄浮的無人機剪影 -->
  <svg class="fly-1" viewBox="0 0 48 48" fill="none">
    <g stroke="#8fa08c" stroke-width="2" stroke-linecap="round">
      <line x1="9" y1="18" x2="21" y2="18"/><line x1="27" y1="18" x2="39" y2="18"/>
      <line x1="15" y1="18" x2="20" y2="23"/><line x1="33" y1="18" x2="28" y2="23"/>
    </g>
    <rect x="19" y="21" width="10" height="7" rx="3" fill="#8fa08c"/>
    <line x1="24" y1="28" x2="24" y2="35" stroke="#8fa08c" stroke-width="1.4"
          stroke-dasharray="2 2"/>
    <rect x="20" y="35" width="8" height="6" rx="1.5" fill="#c2a07d"/>
  </svg>

  <svg class="fly-2" viewBox="0 0 48 48" fill="none">
    <g stroke="#a99e90" stroke-width="2" stroke-linecap="round">
      <line x1="9" y1="20" x2="21" y2="20"/><line x1="27" y1="20" x2="39" y2="20"/>
      <line x1="15" y1="20" x2="20" y2="24"/><line x1="33" y1="20" x2="28" y2="24"/>
    </g>
    <rect x="19" y="22" width="10" height="7" rx="3" fill="#a99e90"/>
  </svg>

  <svg class="fly-3" viewBox="0 0 48 48" fill="none">
    <g stroke="#b98a86" stroke-width="2" stroke-linecap="round">
      <line x1="9" y1="20" x2="21" y2="20"/><line x1="27" y1="20" x2="39" y2="20"/>
      <line x1="15" y1="20" x2="20" y2="24"/><line x1="33" y1="20" x2="28" y2="24"/>
    </g>
    <rect x="19" y="22" width="10" height="7" rx="3" fill="#b98a86"/>
  </svg>

</div>

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
    <div class="alert">庫存偏低，請盡快補貨</div>
    {% endif %}
  </div>

  <div class="card">
    <h2>無人機狀態</h2>
    <div class="drones">
      {% for d in drone_list %}
      <div class="drone {{ d.status }}">
        <svg class="drone-icon" viewBox="0 0 48 48" fill="none" aria-hidden="true">
          {% if d.status == 'busy' %}
            <!-- 派遣中：機體傾斜、旋翼轉動、下方取料光束 -->
            <ellipse class="beam" cx="24" cy="41" rx="9" ry="3" fill="#c2a07d" opacity=".3"/>
            <g stroke="#97744a" stroke-width="2" stroke-linecap="round">
              <line x1="14" y1="17" x2="20" y2="22"/>
              <line x1="34" y1="17" x2="28" y2="22"/>
              <line x1="14" y1="30" x2="20" y2="26"/>
              <line x1="34" y1="30" x2="28" y2="26"/>
            </g>
            <g class="rotor" stroke="#c2a07d" stroke-width="2" stroke-linecap="round">
              <line x1="8"  y1="17" x2="20" y2="17"/>
              <line x1="28" y1="17" x2="40" y2="17"/>
              <line x1="8"  y1="30" x2="20" y2="30"/>
              <line x1="28" y1="30" x2="40" y2="30"/>
            </g>
            <rect x="19" y="20" width="10" height="8" rx="3" fill="#97744a"/>
            <circle cx="24" cy="24" r="1.8" fill="#f0e6d9"/>
            <line x1="24" y1="28" x2="24" y2="36" stroke="#97744a"
                  stroke-width="1.6" stroke-dasharray="2 2"/>
            <rect x="21" y="35" width="6" height="4" rx="1" fill="#97744a"/>
          {% else %}
            <!-- 待命：停在平台上，旋翼靜止 -->
            <g stroke="#8fa08c" stroke-width="2" stroke-linecap="round">
              <line x1="15" y1="19" x2="20" y2="23"/>
              <line x1="33" y1="19" x2="28" y2="23"/>
              <line x1="15" y1="29" x2="20" y2="26"/>
              <line x1="33" y1="29" x2="28" y2="26"/>
            </g>
            <g stroke="#a9b6a5" stroke-width="2" stroke-linecap="round">
              <line x1="10" y1="19" x2="20" y2="19"/>
              <line x1="28" y1="19" x2="38" y2="19"/>
              <line x1="10" y1="29" x2="20" y2="29"/>
              <line x1="28" y1="29" x2="38" y2="29"/>
            </g>
            <rect x="19" y="21" width="10" height="7" rx="3" fill="#6f8a6c"/>
            <circle cx="24" cy="24.5" r="1.7" fill="#e4e9e1"/>
            <line x1="13" y1="38" x2="35" y2="38" stroke="#8fa08c"
                  stroke-width="2.2" stroke-linecap="round"/>
            <line x1="20" y1="28" x2="19" y2="37" stroke="#8fa08c" stroke-width="1.6"/>
            <line x1="28" y1="28" x2="29" y2="37" stroke="#8fa08c" stroke-width="1.6"/>
          {% endif %}
        </svg>

        <div class="drone-body">
          <div class="drone-head">
            <span class="drone-name">無人機 {{ d.id }}</span>
            <span class="drone-state">{{ '派遣中' if d.status == 'busy' else '待命' }}</span>
          </div>
          <div class="drone-order">
            {% if d.status == 'busy' %}
              生產工單 <b>{{ d.order }}</b><br>
              {% if d.required %}需取料 <b>{{ d.required }}</b> 顆　{% endif %}{{ d.since }}
            {% else %}
              停於待命區
            {% endif %}
          </div>
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
</html>"""


@app.route('/')
def index():
    count = latest_data.get("count") or 0
    pct = max(0, min(100, round(count / BAR_FULL_SCALE * 100)))
    if pct <= 10:
        color = "#b98a86"   # 玫瑰灰
    elif pct < 66:
        color = "#c2a07d"   # 陶土
    else:
        color = "#8fa08c"   # 灰綠

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
