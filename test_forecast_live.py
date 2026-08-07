# -*- coding: utf-8 -*-
"""真的打一次 Gemini，驗收 /forecast/analyze 的判斷是否正確。

    set GEMINI_API_KEY=你的金鑰
    python test_forecast_live.py                  <- 打本機
    python test_forecast_live.py https://esp32-scale-server.onrender.com

驗收標準（測試資料是設計過的，答案固定）：
  2025-11 年度總決賽 → 會重複 → 要有正向調整
  2025-07 客戶端缺料 → 一次性 → 絕對不可外推，adjustment 必須是 0
"""
import json
import os
import sys
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")

PAYLOAD = {
    "material": "MTR-001",
    "plant": "P001",
    "history": """年月, SAP預測, 實際銷量
2024-01, 820, 790
2024-02, 810, 850
2024-03, 800, 1380
2024-04, 830, 900
2024-05, 840, 810
2024-06, 850, 870
2024-07, 850, 400
2024-08, 840, 880
2024-09, 830, 850
2024-10, 820, 840
2024-11, 810, 1290
2024-12, 800, 830
2025-01, 810, 800
2025-02, 820, 840
2025-03, 830, 1420
2025-04, 840, 910
2025-05, 850, 830
2025-06, 850, 860""",
    "events": """年月, 事件, 類型
2024-03, 春季錦標賽, 賽事(每年固定)
2024-07, 客戶端缺料停線兩週, 一次性
2024-11, 年度總決賽, 賽事(每年固定)
2025-03, 春季錦標賽, 賽事(每年固定)
2025-11, 年度總決賽(已排定), 賽事(每年固定)
2026-03, 春季錦標賽(已排定), 賽事(每年固定)""",
    "baseline": """年月, SAP統計預測
2025-07, 850
2025-08, 850
2025-09, 840
2025-10, 830
2025-11, 820
2025-12, 810""",
}


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "X-API-Key": os.environ.get("FORECAST_API_KEY", "")},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:   # Render 冷啟動要等
        return json.loads(resp.read().decode("utf-8"))


print("目標:", BASE)
result = post("/forecast/analyze", PAYLOAD)
print(json.dumps(result, ensure_ascii=False, indent=2))

by_month = {a["month"]: a for a in result.get("adjustments", [])}
fails = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  → " + str(extra)) if not cond else ""))
    if not cond:
        fails.append(name)


print("\n--- 驗收 ---")
nov = by_month.get("2025-11")
check("2025-11 有出現在建議裡", nov is not None, sorted(by_month))
if nov:
    check("2025-11 為正向調整", nov["adjustment"] > 0, nov["adjustment"])
    # 規格要的是「說明計算依據」，不是特定字眼。
    # 引用事件名稱或引用實績數字都算，但不可以是「原因不明」還硬調。
    check("2025-11 理由有依據（非原因不明）",
          "原因不明" not in nov["reason"] and
          (any(k in nov["reason"] for k in ("決賽", "賽事", "賽")) or
           any(c.isdigit() for c in nov["reason"])), nov["reason"])

jul = by_month.get("2025-07")
check("2025-07 有出現在建議裡", jul is not None, sorted(by_month))
if jul:
    check("2025-07 完全不調整（一次性事件不可外推）",
          jul["adjustment"] == 0, jul["adjustment"])

one_off = [a for a in result.get("analysis", []) if a.get("month") == "2024-07"]
if one_off:
    check("2024-07 被判為不會重複", one_off[0].get("recurring") is False,
          one_off[0])

if result.get("warnings"):
    print("\n提醒:")
    for w in result["warnings"]:
        print("  [!]", w)

print("\n" + ("驗收通過" if not fails else "失敗 %d 項: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
