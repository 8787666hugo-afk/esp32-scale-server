# -*- coding: utf-8 -*-
"""SAP 銷售預測 — AI 調整建議

    POST /forecast/analyze           ABAP 送歷史+事件+基線 → Gemini → 存結果
    GET  /forecast/result/<material> ABAP 取回調整建議

刻意獨立成一個檔：把原本的 app 匯進來再掛上新端點，
server.py 一個字都不用改，磅秤看板那條線完全不受影響。

啟動方式（注意進入點是這個檔，不是 server）：
    gunicorn forecast_api:app        <- Render 的 Start Command 要改成這個
    python forecast_api.py           <- 本機測試
"""
import json
import os
from datetime import datetime

from flask import jsonify, request

# 原本的服務原封不動地拿過來用
from server import add_alert, app

# 配額是按模型分別算的，換模型可能繞過 429。
# 設環境變數 GEMINI_MODEL 就能換，不必改程式碼。
#
# 釘死版本而不是用 gemini-flash-latest：稽核紀錄要能追溯當時是哪個模型
# 給的建議，別名會在腳下換掉。
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

# 設了才驗；沒設就放行，本機測試方便。送真實資料前務必在 Render 設好。
FORECAST_API_KEY = os.environ.get("FORECAST_API_KEY", "")

# 最近幾個料號的分析結果，key 為料號
forecast_results = {}
MAX_FORECAST = 20

# 強制 JSON 輸出。ABAP 解析不了自由格式，這段不能省。
FORECAST_SCHEMA = {
    "type": "object",
    "properties": {
        "material": {"type": "string"},
        "plant": {"type": "string"},
        "analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "month": {"type": "string"},
                    "forecast": {"type": "integer"},
                    "actual": {"type": "integer"},
                    "error_pct": {"type": "number"},
                    "event": {"type": "string"},
                    "recurring": {"type": "boolean"},
                },
                "required": ["month", "error_pct", "event", "recurring"],
            },
        },
        "adjustments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "month": {"type": "string"},
                    "baseline": {"type": "integer"},
                    "adjustment": {"type": "integer"},
                    "final": {"type": "integer"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "string",
                                   "enum": ["high", "medium", "low"]},
                },
                "required": ["month", "baseline", "adjustment", "final",
                             "reason", "confidence"],
            },
        },
    },
    "required": ["material", "analysis", "adjustments"],
}

FORECAST_SYSTEM_PROMPT = """你是製造業的需求規劃分析師，負責檢視 SAP 統計預測與實績的落差，並提出調整建議。

【分析規則】
1. 找出誤差絕對值超過 25% 的月份，與事件清單配對
2. 判斷每個事件是「會重複」（每年固定發生）還是「一次性」
3. 只有會重複的事件，才能作為未來期間的調整依據
4. 一次性事件（疫情、突發缺料、單一專案）絕對不可外推

【調整規則】
- adjustment 可以是負數（例如舊型號被新品蠶食）
- final = baseline + adjustment，且不得小於 0
- reason 必須說明計算依據，例如「參考 2024/2025 同期實績平均高於基線 65%」
- 找不到明確原因時，adjustment 一律填 0，reason 填「原因不明，不調整」
- 嚴禁臆測理由。沒有證據就是沒有證據。

【事件週期】
事件分三種週期，判斷方式不同：
- recurring-annual（每年固定）：每年同月都會發生，直接套用
- recurring-biennial（每兩年一次）：只在特定年份發生。看年份規律，
  2024 有、2025 沒有 → 2026 會有。不可因為去年沒發生就判定為一次性。
  IFMAR World Championship 屬於此類，2024 舉辦，2026 再度舉辦。
- one-off（一次性）：疫情、突發缺料、匯率波動、經銷商倒閉、單一專案。
  絕對不可外推。

【未列出的未來月份】
事件清單只涵蓋已知事件。若某個未來月份沒有條目，但同一 biennial 週期的
對應月份（兩年前的同月）有事件，要以該月份的歷史影響幅度作為調整依據，
並在 reason 中寫明是參照哪一年哪一個月推導出來的。

【信心度判定】
- high:   事件連續兩年以上重複，且影響幅度穩定
- medium: 事件重複但幅度變動大，或只有一年資料
- low:    調整量超過 baseline 的 GUARD_PCT%，或事件性質不確定
"""

# 調整幅度超過 baseline 這個百分比就降為 low confidence。
# 預設 50 是刻意的：IFMAR +65% 會觸發，大額調整本來就該有人看過。
# 要放寬就在 Render 設環境變數 GUARD_PCT，不必改程式碼。
GUARD_PCT = int(os.environ.get("GUARD_PCT", "50"))

# 事件清單（選項 B）。ABAP 沒送 events 時用這份。
# 來源：SAVOX_Forecast_Data_v2.xlsx 的 "Events for AI Analysis" 分頁。
#
# ABAP 那支程式的 P_EVTAB 決定用哪一份：
#   打勾   → ABAP 送自己的 get_events_manual，這裡不會被用到
#   不打勾 → ABAP 送空的，就用下面這份
# 兩條路餵給 Gemini 的 prompt 完全相同，差別只在改一次要重貼 ABAP
# 還是 git push。
#
# 注意 2026-09 刻意沒有填影響幅度：IFMAR 2026 還沒發生，要讓 AI 自己
# 從 2024-09 推導。那個推導就是整個 Demo 要證明的事。
EVENT_LIBRARY = {
    "SERVO-RACE-03": """month, event, type, impact
2023-03, ROAR Nationals USA, recurring-annual, +25%
2023-05, Rare earth magnet shortage, one-off, -22%
2023-07, New SE Asia distributor onboarded, one-off, +20%
2023-10, EU distributor Q4 clearance pull-forward, one-off, +18%
2024-04, Firmware upgrade buzz, one-off, +22%
2024-06, IFMAR World Championship pre-order wave 1, recurring-biennial, +55%
2024-07, IFMAR World Championship pre-order wave 2, recurring-biennial, +45%
2024-09, IFMAR World Championship EVENT MONTH, recurring-biennial, +65%
2024-10, Post-IFMAR restocking, recurring-biennial, +20%
2024-11, EU distributor bankruptcy, one-off, -37%
2025-01, USD/TWD spike 8 percent, one-off, -32%
2025-03, ROAR Nationals plus delayed Jan orders, recurring-annual, +28%
2025-06, Factory planned maintenance one week, one-off, -17%
2025-08, IFMAR 2026 early prep begins, recurring-biennial, +22%
2025-09, IFMAR 2026 prep accelerates, recurring-biennial, +28%
2025-11, Competitor Hitec premium line announced, one-off, -20%
2025-12, Year-end stocking, recurring-annual, +22%
2026-06, IFMAR 2026 pre-order wave 1, recurring-biennial, +55%
2026-07, IFMAR 2026 pre-order wave 2 peak, recurring-biennial, +62%
2026-09, IFMAR 2026 World Championship SCHEDULED, recurring-biennial, not yet observed
""",
}
# 舊料號代碼指向同一份，換料號時不必兩邊改
EVENT_LIBRARY["MOTOR-001"] = EVENT_LIBRARY["SERVO-RACE-03"]
EVENT_LIBRARY["MTR-001"] = EVENT_LIBRARY["SERVO-RACE-03"]


def as_text(value):
    """ABAP 送純文字，也接受字串陣列。"""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v) for v in value)
    return str(value or "").strip()


def forecast_key_denied():
    """沒設 FORECAST_API_KEY 就不驗。回傳錯誤訊息，通過則回 None。"""
    if not FORECAST_API_KEY:
        return None
    if request.headers.get("X-API-Key", "") != FORECAST_API_KEY:
        return "X-API-Key 不正確"
    return None


def call_gemini(material, plant, history, events, baseline):
    # 延後 import：沒裝 google-genai 時，既有的看板端點照樣能跑
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("伺服器未設定 GEMINI_API_KEY")

    prompt = f"""【料號】{material}  【工廠】{plant}

【歷史資料：預測 vs 實績】
{history}

【事件清單】
{events}

【SAP 統計預測基線】
{baseline}

請依規則分析歷史誤差並提出未來各期的調整建議。"""

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            # 門檻寫在 prompt 裡是佔位符，這裡換成實際值，
            # 免得改了 GUARD_PCT 但 AI 還按舊數字判信心度
            system_instruction=FORECAST_SYSTEM_PROMPT.replace(
                "GUARD_PCT", str(GUARD_PCT)),
            temperature=0,               # 要可重現，不要創意
            response_mime_type="application/json",
            response_schema=FORECAST_SCHEMA,
        ),
    )
    return json.loads(resp.text)


def validate_adjustments(result):
    """寫回 SAP 前的最後一關。不合格的直接剔除，剩下的才給 ABAP。"""
    kept = []
    problems = []
    for a in result.get("adjustments", []):
        month = a.get("month", "?")
        baseline = a.get("baseline", 0) or 0
        adjustment = a.get("adjustment", 0) or 0
        final = a.get("final", 0) or 0

        if final != baseline + adjustment:
            problems.append(f"{month}: final 不等於 baseline + adjustment，已剔除")
            continue
        if not str(a.get("reason", "")).strip():
            problems.append(f"{month}: reason 為空，已剔除")
            continue
        if final < 0:
            a["final"] = 0
            problems.append(f"{month}: final 為負數，已修正為 0")
        if baseline > 0 and abs(adjustment) * 100 > baseline * GUARD_PCT:
            a["confidence"] = "low"
            problems.append(f"{month}: 調整幅度超過 baseline 的 {GUARD_PCT}%，"
                            "信心度降為 low，需人工確認")
        kept.append(a)

    result["adjustments"] = kept
    return problems


@app.route('/forecast/analyze', methods=['POST'])
def forecast_analyze():
    """ABAP 送歷史、事件、基線 → 呼叫 Gemini → 存下調整建議。

    body: {"material": "MTR-001", "plant": "P001",
           "history":  "年月, SAP預測, 實際銷量\\n2024-01, 820, 790\\n...",
           "events":   "年月, 事件, 類型\\n2024-03, 春季錦標賽, 賽事(每年固定)\\n...",
           "baseline": "年月, SAP統計預測\\n2025-07, 850\\n..."}
    三段資料送字串或字串陣列都可以。
    回傳的內容與 /forecast/result/<material> 相同，ABAP 可以直接用。
    """
    denied = forecast_key_denied()
    if denied:
        return jsonify({"error": denied}), 401

    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        # 送進來的不是 JSON 物件（陣列、字串、或根本解不開）。
        # 直接 .get 會炸成沒有資訊的 500，所以先擋下來並回報實際收到什麼。
        raw = request.get_data(as_text=True)
        return jsonify({
            "error": "body 必須是 JSON 物件",
            "content_type": request.headers.get("Content-Type", ""),
            "received_type": type(body).__name__,
            "length": len(raw),
            "preview": raw[:200],
        }), 400

    material = str(body.get("material", "")).strip().upper()
    if not material:
        return jsonify({"error": "缺少 material"}), 400

    plant = str(body.get("plant", "")).strip()
    history = as_text(body.get("history"))
    events = as_text(body.get("events"))
    baseline = as_text(body.get("baseline"))
    if not history or not baseline:
        return jsonify({"error": "缺少 history 或 baseline"}), 400
    if not events:
        # ABAP 沒送（P_EVTAB 沒打勾，選項 B）就用內建清單。
        # 連內建都沒有才真的無從歸因，reason 會全是「原因不明」。
        events = EVENT_LIBRARY.get(material, "（未提供事件清單）")
        event_source = "server" if material in EVENT_LIBRARY else "none"
    else:
        event_source = "abap"

    try:
        result = call_gemini(material, plant, history, events, baseline)
    except Exception as exc:
        add_alert("short", f"{material} 預測分析失敗", str(exc))
        return jsonify({"error": f"呼叫 Gemini 失敗：{exc}"}), 502

    problems = validate_adjustments(result)
    result["material"] = result.get("material") or material
    result["plant"] = result.get("plant") or plant
    result["warnings"] = problems
    result["model"] = GEMINI_MODEL          # 稽核用：半年後查問題會需要
    result["event_source"] = event_source   # 事件清單是誰給的：abap / server / none
    result["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    forecast_results.pop(material, None)    # 重跑時移到最後，才不會被當成舊的清掉
    forecast_results[material] = result
    for stale in list(forecast_results)[:-MAX_FORECAST]:
        del forecast_results[stale]

    detail = f"{len(result['adjustments'])} 期調整建議"
    if problems:
        detail += f"，{len(problems)} 項提醒"
    add_alert("info", f"{material} 預測分析完成", detail)
    return jsonify(result), 200


@app.route('/forecast/models', methods=['GET'])
def forecast_models():
    """列出這把金鑰能用的模型。

    純診斷用。429 limit:0 常常是模型退出免費層造成的，
    與其猜模型名稱，不如直接問 Google。
    """
    denied = forecast_key_denied()
    if denied:
        return jsonify({"error": denied}), 401

    try:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("伺服器未設定 GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)
        names = []
        for m in client.models.list():
            actions = list(getattr(m, "supported_actions", None) or [])
            if not actions or "generateContent" in actions:
                names.append(getattr(m, "name", str(m)))
    except Exception as exc:
        return jsonify({"error": f"列出模型失敗：{exc}"}), 502

    return jsonify({"current": GEMINI_MODEL, "count": len(names),
                    "models": sorted(names)})


@app.route('/forecast/result/<material>', methods=['GET'])
def forecast_result(material):
    """ABAP 取回最近一次的調整建議。"""
    denied = forecast_key_denied()
    if denied:
        return jsonify({"error": denied}), 401

    result = forecast_results.get(material.strip().upper())
    if result is None:
        return jsonify({"error": f"查無 {material} 的分析結果，"
                                 "請先呼叫 /forecast/analyze"}), 404
    return jsonify(result)


@app.errorhandler(Exception)
def forecast_unhandled(exc):
    """讓 /forecast 的未攔截例外回 JSON 而不是 Flask 的 HTML 錯誤頁。

    ABAP 那端只看得到 500 和一坨 HTML，除錯等於瞎猜。
    非 /forecast 的路徑維持原本行為，不影響既有看板。
    """
    if not request.path.startswith('/forecast'):
        raise exc
    code = getattr(exc, "code", 500)
    if code != 500:
        raise exc
    import traceback
    tb = traceback.format_exc().strip().splitlines()
    add_alert("short", "預測端點未攔截的錯誤", f"{type(exc).__name__}: {exc}")
    return jsonify({"error": f"伺服器內部錯誤：{type(exc).__name__}: {exc}",
                    "traceback": tb[-6:]}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
