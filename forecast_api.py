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
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

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

【信心度判定】
- high:   事件連續兩年以上重複，且影響幅度穩定
- medium: 事件重複但幅度變動大，或只有一年資料
- low:    調整量超過 baseline 的 50%，或事件性質不確定
"""


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
            system_instruction=FORECAST_SYSTEM_PROMPT,
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
        if baseline > 0 and abs(adjustment) > baseline * 0.5:
            a["confidence"] = "low"
            problems.append(f"{month}: 調整幅度超過 baseline 的 50%，"
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

    body = request.get_json(force=True, silent=True) or {}
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
        # 沒有事件清單，AI 無從歸因，reason 只會是「原因不明」
        events = "（未提供事件清單）"

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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
