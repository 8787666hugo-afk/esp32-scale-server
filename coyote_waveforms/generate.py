"""產生郊狼 (DG-LAB Coyote 3.0) 波形：JSON、App 用的 .pulse 檔與 SVG 預覽圖。

每個波形用 25ms 為一個子步驟描述 (頻率週期 ms, 強度 %)，
每 4 個子步驟打包成 V3 協議的一段 100ms 波形 HEX 字串：
前 4 個 byte 是頻率、後 4 個 byte 是強度。

執行: python3 generate.py
"""

import json
from pathlib import Path

OUT = Path(__file__).parent
STEP_MS = 25


def freq_to_byte(period_ms):
    """V3 協議的頻率壓縮：輸入 10~1000ms，輸出 10~240。"""
    p = max(10, min(1000, round(period_ms)))
    if p <= 100:
        return p
    if p <= 600:
        return (p - 100) // 5 + 100
    return (p - 600) // 10 + 200


def hold(period_ms, intensity, duration_ms):
    return [(period_ms, intensity)] * (duration_ms // STEP_MS)


def ramp(period_ms, i_from, i_to, duration_ms):
    n = duration_ms // STEP_MS
    return [(period_ms, i_from + (i_to - i_from) * i / (n - 1)) for i in range(n)]


def staircase():
    """圖一：實心階梯 20→40→60→80→100%，頂端停留後休息。"""
    steps = []
    for level in (20, 40, 60, 80):
        steps += hold(10, level, 200)
    steps += hold(10, 100, 600)
    steps += hold(10, 0, 600)
    return steps


def stepped_peaks():
    """圖二：實心，階梯爬到 100% 再階梯下降，然後短暫停頓。"""
    steps = []
    for level in (30, 45, 60, 75, 90, 100, 100, 90, 80, 70, 60):
        steps += hold(10, level, 100)
    steps += hold(10, 0, 200)
    return steps


def ramp_hold():
    """圖三改成實心：原本「稀疏→變密」的漸強感改用強度 40→100% 平滑上升，再維持 100%。"""
    steps = ramp(10, 40, 100, 1200)
    steps += hold(10, 100, 1200)
    return steps


def pack(steps):
    assert len(steps) % 4 == 0, "每段 100ms 需要 4 個子步驟"
    frames = []
    for i in range(0, len(steps), 4):
        chunk = steps[i:i + 4]
        freqs = [freq_to_byte(p) for p, _ in chunk]
        levels = [max(0, min(100, round(v))) for _, v in chunk]
        frames.append("".join(f"{b:02X}" for b in freqs + levels))
    return frames


def pulse_section(steps):
    """DG-LAB App .pulse 小節：頻率 A,頻率 B,時長,頻率模式,開關/強度-錨點,...

    頻率索引 0 = 10ms (實心)，模式 1 = 固定頻率，時長索引 0 = 形狀只播一次。
    每個強度點代表 0.1 秒；數值改變的位置標成錨點 (-1)，其餘為 -0。
    """
    assert len(steps) % 4 == 0 and all(p == 10 for p, _ in steps), "只支援實心波形"
    levels = [round(sum(v for _, v in steps[i:i + 4]) / 4, 2) for i in range(0, len(steps), 4)]
    points = []
    for i, v in enumerate(levels):
        edge = i in (0, len(levels) - 1)
        corner = 0 < i < len(levels) - 1 and abs(
            (v - levels[i - 1]) - (levels[i + 1] - v)) > 0.05
        points.append(f"{v:.2f}-{1 if edge or corner else 0}")
    return "0,0,0,1,1/" + ",".join(points)


def pulse_file(sections):
    """休息 0、速度 1x；休息時間直接寫成強度 0 的點，跟 JSON 版本一致。"""
    return "Dungeonlab+pulse:0,1,16=" + "+section+".join(sections)


def svg_preview(steps, title):
    """仿 App 波形編輯器：每條豎線是一個脈衝，間距 = 頻率週期，高度 = 強度。"""
    px_per_ms = 0.4
    height = 120
    total_ms = len(steps) * STEP_MS * 2  # 畫兩個循環，方便看重複
    width = total_ms * px_per_ms
    lines = []
    t = 0.0
    while t < total_ms:
        period, level = steps[int(t // STEP_MS) % len(steps)]
        if level > 0:
            x = t * px_per_ms
            h = level / 100 * (height - 10)
            lines.append(
                f'<rect x="{x:.1f}" y="{height - h:.1f}" '
                f'width="{max(1.2, min(4, period * px_per_ms * 0.5)):.1f}" '
                f'height="{h:.1f}"/>'
            )
        t += period
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height + 24}" viewBox="0 0 {width:.0f} {height + 24}">'
        f'<rect width="100%" height="100%" fill="#1b1b1b"/>'
        f'<text x="8" y="16" fill="#ccc" font-size="13" font-family="sans-serif">'
        f'{title}</text>'
        f'<g transform="translate(0,22)" fill="#f5e3a3">{"".join(lines)}</g></svg>'
    )


WAVEFORMS = [
    ("01_staircase", "階梯漸強", "實心階梯 20→40→60→80→100%，頂端停 0.6 秒後休息 0.6 秒", staircase),
    ("02_stepped_peaks", "階梯波峰", "實心，階梯升到 100% 再降到 60%，停 0.2 秒", stepped_peaks),
    ("03_ramp_hold", "平滑漸強", "實心，強度 40→100% 平滑上升 1.2 秒，再維持 100% 1.2 秒", ramp_hold),
]


def main():
    combined = {}
    sequence = []
    sections = []
    for slug, name, desc, build in WAVEFORMS:
        steps = build()
        pulse = pack(steps)
        data = {
            "name": name,
            "description": desc,
            "protocol": "DG-LAB Coyote V3 (B0)，每段 100ms：4 byte 頻率 + 4 byte 強度",
            "durationMs": len(pulse) * 100,
            "pulseData": pulse,
        }
        (OUT / f"{slug}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        section = pulse_section(steps)
        sections.append(section)
        (OUT / f"{slug}.pulse").write_text(pulse_file([section]), encoding="utf-8")
        (OUT / f"{slug}.svg").write_text(svg_preview(steps, name), encoding="utf-8")
        combined[name] = pulse
        sequence += pulse
    joined = {
        "name": "三段連續",
        "description": "階梯漸強 → 階梯波峰 → 平滑漸強，依序串成一條",
        "protocol": "DG-LAB Coyote V3 (B0)，每段 100ms：4 byte 頻率 + 4 byte 強度",
        "durationMs": len(sequence) * 100,
        "pulseData": sequence,
    }
    (OUT / "04_all_in_one.json").write_text(
        json.dumps(joined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "04_all_in_one.pulse").write_text(pulse_file(sections), encoding="utf-8")
    (OUT / "all_waveforms.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
