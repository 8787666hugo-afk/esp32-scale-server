# 教材「動手作」：用 pygame.draw 畫出麵包超人
import math
import pygame as pg

pg.init()

# 設定視窗
width, height = 640, 480
screen = pg.display.set_mode((width, height))
pg.display.set_caption("Anpanman")

# 顏色
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
FACE = (236, 150, 90)
FACE_LIGHT = (246, 178, 122)
RED = (225, 30, 30)

def arc(surface, color, rect, start, end, width):
    """pg.draw.arc 線寬大時會有破洞，改成沿著橢圓取點用 lines 連起來，參數跟 pg.draw.arc 一樣"""
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    n = 40
    points = []
    for i in range(n + 1):
        a = start + (end - start) * i / n
        points.append((cx + w / 2 * math.cos(a), cy - h / 2 * math.sin(a)))
    pg.draw.lines(surface, color, False, points, width)
    for pt in (points[0], points[-1]):          # 線頭畫圓，讓端點圓滑
        pg.draw.circle(surface, color, pt, width // 2)


# 建立畫布bg
bg = pg.Surface(screen.get_size())
bg = bg.convert()
bg.fill(WHITE)

# 臉（底色 + 亮面）
pg.draw.circle(bg, FACE, (320, 240), 160, 0)
pg.draw.ellipse(bg, FACE_LIGHT, [210, 100, 220, 200], 0)

# 眉毛（上半圓弧）
arc(bg, BLACK, [225, 125, 90, 70], 0.3, math.pi - 0.3, 5)
arc(bg, BLACK, [325, 125, 90, 70], 0.3, math.pi - 0.3, 5)

# 眼睛（黑色橢圓 + 白色反光）
pg.draw.ellipse(bg, BLACK, [258, 168, 26, 42], 0)
pg.draw.ellipse(bg, BLACK, [356, 168, 26, 42], 0)
pg.draw.circle(bg, WHITE, (266, 180), 4, 0)
pg.draw.circle(bg, WHITE, (364, 180), 4, 0)

# 嘴巴（下半圓弧，先畫，讓鼻子蓋在上面）
arc(bg, BLACK, [245, 250, 150, 110], math.pi + 0.35, 2 * math.pi - 0.35, 5)

# 臉頰與鼻子（紅色圓 + 黑框），鼻子最後畫才會疊在臉頰上
for x, r in [(212, 55), (428, 55), (320, 55)]:
    pg.draw.circle(bg, RED, (x, 268), r, 0)
    pg.draw.circle(bg, BLACK, (x, 268), r, 3)
    pg.draw.rect(bg, WHITE, [x - 8, 256, 16, 18], 0)   # 白色反光方塊

# 嘴角的小皺紋
arc(bg, BLACK, [232, 318, 30, 30], math.pi / 2, math.pi * 1.2, 4)
arc(bg, BLACK, [378, 318, 30, 30], -math.pi * 0.2, math.pi / 2, 4)

# 加入文字
font = pg.font.SysFont("arial", 24)
text = font.render("Anpanman", True, (0, 0, 255), WHITE)
bg.blit(text, (270, 420))

# 要在這兩條程式碼上面喔!!
screen.blit(bg, (0, 0))
pg.display.update()
pg.image.save(screen, "anpanman.png")

# 關閉程式的程式碼
running = True
while running:
    for event in pg.event.get():
        if event.type == pg.QUIT:
            running = False
pg.quit()
