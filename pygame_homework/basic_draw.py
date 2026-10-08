# 教材「基本繪圖」範例：矩形、圓形、橢圓形、圓弧形、直線
import pygame as pg

pg.init()

# 設定視窗
width, height = 640, 480
screen = pg.display.set_mode((width, height))
pg.display.set_caption("Basic Draw")

# 建立畫布bg
bg = pg.Surface(screen.get_size())
bg = bg.convert()
bg.fill((255, 255, 255))

# 繪製幾何圖形
pg.draw.rect(bg, (0, 0, 255), [70, 70, 500, 60], 4)              # 空心矩形
pg.draw.rect(bg, (0, 0, 255), [70, 150, 500, 60], 0)             # 實心矩形
pg.draw.circle(bg, (0, 0, 255), (100, 300), 50, 4)               # 圓形
pg.draw.ellipse(bg, (0, 0, 255), [200, 250, 150, 80], 4)         # 橢圓形
pg.draw.arc(bg, (0, 0, 255), [400, 250, 70, 150], 5, 1.5, 4)     # 圓弧形
pg.draw.line(bg, (0, 0, 255), (550, 250), (550, 400), 4)         # 直線

# 要在這兩條程式碼上面喔!!
screen.blit(bg, (0, 0))
pg.display.update()
pg.image.save(screen, "basic_draw.png")

# 關閉程式的程式碼
running = True
while running:
    for event in pg.event.get():
        if event.type == pg.QUIT:
            running = False
pg.quit()
