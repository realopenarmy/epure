"""Épure avatar, two variants, drawn at 4x and downsampled. python make_avatar.py"""
import math
from PIL import Image, ImageDraw, ImageFont
S = 4; W = 1024 * S
SERIF = '/usr/share/fonts/truetype/sand-box/google/Cormorant Garamond/CormorantGaramond-VariableFont_wght.ttf'

def P(*xy): return [v * S for v in xy]

def arrow(d, tip, ang, col, L=26, Wd=7):
    x, y = tip; c, s = math.cos(ang), math.sin(ang)
    bx, by = x - L*c, y - L*s
    d.polygon(P(x, y, bx - Wd*s, by + Wd*c, bx + Wd*s, by - Wd*c), fill=col)

def dashed(d, a, b, col, w, dash, gap):
    (x0, y0), (x1, y1) = a, b; Ltot = math.dist(a, b); t = 0
    while t < Ltot:
        t2 = min(t + dash, Ltot)
        d.line(P(x0+(x1-x0)*t/Ltot, y0+(y1-y0)*t/Ltot, x0+(x1-x0)*t2/Ltot, y0+(y1-y0)*t2/Ltot), fill=col, width=w*S)
        t = t2 + gap

def chain(d, a, b, col, w):
    (x0, y0), (x1, y1) = a, b; Ltot = math.dist(a, b); t = 0; pat = [(40, 10), (6, 10)]; i = 0
    while t < Ltot:
        dl, g = pat[i % 2]; t2 = min(t + dl, Ltot)
        d.line(P(x0+(x1-x0)*t/Ltot, y0+(y1-y0)*t/Ltot, x0+(x1-x0)*t2/Ltot, y0+(y1-y0)*t2/Ltot), fill=col, width=w*S)
        t = t2 + g; i += 1

def variant_a(path):
    bg, ink = (18, 52, 96), (236, 232, 220)
    faint = (40, 78, 124)
    im = Image.new('RGB', (W, W), bg); d = ImageDraw.Draw(im)
    for g in range(0, 1025, 64):                      # faint drafting grid
        d.line(P(g, 0, g, 1024), fill=faint, width=S); d.line(P(0, g, 1024, g), fill=faint, width=S)
    # front view of a stepped part
    out = [(300, 700), (724, 700), (724, 610), (624, 610), (624, 470), (400, 470), (400, 610), (300, 610), (300, 700)]
    d.line([v * S for p in out for v in p], fill=ink, width=6*S, joint='curve')
    for x in (482, 542):                               # hidden bore
        dashed(d, (x, 470), (x, 700), ink, 3, 18, 10)
    chain(d, (512, 440), (512, 730), ink, 2)           # centreline
    # one dimension line over the boss, value is the É
    for x in (400, 624):
        d.line(P(x, 455, x, 352), fill=ink, width=2*S)
    d.line(P(400, 372, 624, 372), fill=ink, width=2*S)
    arrow(d, (400, 372), math.pi, ink); arrow(d, (624, 372), 0, ink)
    f = ImageFont.truetype(SERIF, 150 * S); f.set_variation_by_axes([500])
    d.text((512*S, 356*S), 'É', font=f, fill=ink, anchor='ms')
    im.resize((1024, 1024), Image.LANCZOS).save(path)

def variant_b(path):
    bg, ink, acc = (245, 242, 234), (22, 44, 82), (176, 58, 46)
    im = Image.new('RGB', (W, W), bg); d = ImageDraw.Draw(im)
    hinge, needle, pencil = (512, 210), (380, 740), (644, 740)
    R = math.dist(needle, pencil)
    # arc being drawn around the needle point
    box = P(needle[0]-R, needle[1]-R, needle[0]+R, needle[1]+R)
    d.arc(box, 322, 360, fill=ink, width=4*S)
    for a0 in range(296, 320, 8):                       # dashed lead-in
        d.arc(box, a0, a0 + 4, fill=ink, width=3*S)
    # centre mark at the needle
    d.line(P(needle[0]-34, needle[1], needle[0]+34, needle[1]), fill=ink, width=2*S)
    d.line(P(needle[0], needle[1]-34, needle[0], needle[1]+34), fill=ink, width=2*S)
    # radius dimension: needle -> pencil with an arrow at the arc
    d.line(P(needle[0], needle[1], pencil[0]-4, pencil[1]), fill=acc, width=2*S)
    arrow(d, pencil, 0, acc, 24, 7)
    # compass legs (thin double strokes), handle, hinge
    for foot in (needle, pencil):
        ang = math.atan2(foot[1]-hinge[1], foot[0]-hinge[0]); nx, ny = -math.sin(ang)*9, math.cos(ang)*9
        d.polygon(P(hinge[0]+nx, hinge[1]+ny, foot[0]+nx*0.25, foot[1]-28+ny*0.25, foot[0], foot[1],
                    foot[0]-nx*0.25, foot[1]-28-ny*0.25, hinge[0]-nx, hinge[1]-ny), outline=ink, width=3*S)
    d.line(P(hinge[0], hinge[1]-34, hinge[0], hinge[1]-84), fill=ink, width=10*S)
    d.ellipse(P(hinge[0]-26, hinge[1]-26, hinge[0]+26, hinge[1]+26), fill=bg, outline=ink, width=4*S)
    d.ellipse(P(hinge[0]-7, hinge[1]-7, hinge[0]+7, hinge[1]+7), fill=ink)
    # cross-bar
    t = 0.55; a = (hinge[0]+(needle[0]-hinge[0])*t, hinge[1]+(needle[1]-hinge[1])*t)
    b = (hinge[0]+(pencil[0]-hinge[0])*t, hinge[1]+(pencil[1]-hinge[1])*t)
    im.resize((1024, 1024), Image.LANCZOS).save(path)

if __name__ == '__main__':
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else '.'
    variant_a(f'{out}/avatar_a.png'); variant_b(f'{out}/avatar_b.png')
