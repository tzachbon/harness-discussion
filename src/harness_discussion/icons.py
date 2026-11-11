"""The app icon, drawn in code so every size stays sharp."""
import math

from PIL import Image, ImageDraw

BACK = "#3b4bd8"


def icon_image(size=64):
    """Two speech bubbles on an indigo rounded square, the front one with sound bars.
    Drawn 4x larger on a 64-unit grid and scaled down for smooth edges."""
    k = size * 4 / 64
    img = Image.new("RGBA", (size * 4, size * 4))
    d = ImageDraw.Draw(img)

    def box(x0, y0, x1, y1):
        return x0 * k, y0 * k, x1 * k, y1 * k

    def poly(points):
        return [(x * k, y * k) for x, y in points]

    d.rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=14 * k, fill=BACK)
    # back bubble, tail to the lower right
    d.rounded_rectangle(box(27, 12, 56, 34), radius=8 * k, fill="#a9b4ff")
    d.polygon(poly([(44, 34), (53, 30), (55, 41)]), fill="#a9b4ff")
    # front bubble, tail to the lower left, with a gap cut around it so the two read apart
    d.rounded_rectangle(box(5, 21, 45, 51), radius=10 * k, fill=BACK)
    d.rounded_rectangle(box(8, 24, 42, 48), radius=8 * k, fill="white")
    d.polygon(poly([(12, 45), (22, 46), (10, 56)]), fill="white")
    for x, half in ((16, 4), (23, 8), (30, 4)):  # sound bars, centred on y = 36
        d.rounded_rectangle(box(x, 36 - half, x + 4, 36 + half), radius=2 * k, fill=BACK)
    return img.resize((size, size), Image.LANCZOS)


def _canvas(size):
    """A 4x image on a 64-unit grid, and helpers to scale grid points onto it."""
    k = size * 4 / 64
    img = Image.new("RGBA", (size * 4, size * 4))
    return img, ImageDraw.Draw(img), (lambda *v: tuple(n * k for n in v)), k


def source_image(name, size=32):
    """The voice source's mark on a rounded square, so the bar and Settings show sources at a glance."""
    img, d, g, k = _canvas(size)
    back = {"Cursor": "#111111", "Codex": "#0d0d0d", "Edge": "#0b5cad", "Windows": "#0078d4",
            "Kokoro": "#e8590c"}.get(name, "#5e5ce6")
    d.rounded_rectangle(g(0, 0, 63.9, 63.9), radius=15 * k, fill=back)
    if name == "Cursor":  # an isometric cube, three shaded faces
        top, left, right, mid, low = g(32, 11), g(14, 21.5), g(50, 21.5), g(32, 32), g(32, 53)
        d.polygon([top, right, mid, left], fill="#f2f2f2")
        d.polygon([left, mid, low, g(14, 42.5)], fill="#9b9b9b")
        d.polygon([mid, right, g(50, 42.5), low], fill="#575757")
    elif name == "Codex":  # a terminal prompt
        d.line([g(16, 21), g(29, 32), g(16, 43)], fill="white", width=round(6 * k), joint="curve")
        d.rounded_rectangle(g(33, 40, 49, 46), radius=3 * k, fill="white")
    elif name == "Edge":  # a swirl
        d.ellipse(g(11, 11, 53, 53), fill="#33c481")
        d.ellipse(g(20, 22, 48, 50), fill=back)
        d.rectangle(g(34, 30, 54, 37), fill=back)
    elif name == "Windows":
        for x, y in ((16, 16), (33, 16), (16, 33), (33, 33)):
            d.rectangle(g(x, y, x + 15, y + 15), fill="white")
    else:  # macOS, Kokoro and any other on-device voice: a waveform
        for x, half in ((14, 6), (22, 13), (30, 19), (38, 11), (46, 5)):
            d.rounded_rectangle(g(x, 32 - half, x + 5, 32 + half), radius=2.5 * k, fill="white")
    return img.resize((size, size), Image.LANCZOS)


def menu_bar_image(size=36):
    """The menu bar mark: the app icon's speech bubble with sound bars cut out, black on clear for a template."""
    _, _, g, k = _canvas(size)
    mask = Image.new("L", (size * 4, size * 4))
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle(g(4, 8, 60, 48), radius=13 * k, fill=255)
    d.polygon([g(12, 44), g(28, 46), g(9, 60)], fill=255)
    for x, half in ((17, 6), (25, 12), (33, 9), (41, 4)):
        d.rounded_rectangle(g(x, 28 - half, x + 5, 28 + half), radius=2.5 * k, fill=0)
    img = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    img.putalpha(mask)
    return img.resize((size, size), Image.LANCZOS)


PAGE_COLORS = {"Voices": "#8e5cf0", "Captions": "#14a3a3", "Reading": "#2f7ff2", "Summarize": "#e8467c", "Meetings": "#16a34a",
               "Speech to Text": "#f08a24", "General": "#7a7f8a"}


def page_image(name, size=20):
    """A Settings sidebar mark: a white glyph on the page's colored rounded square."""
    img, d, g, k = _canvas(size)
    d.rounded_rectangle(g(0, 0, 63.9, 63.9), radius=15 * k, fill=PAGE_COLORS.get(name, "#7a7f8a"))
    w = round(6 * k)
    if name == "Voices":
        for x, half in ((14, 7), (24, 16), (34, 11), (44, 5)):
            d.rounded_rectangle(g(x, 32 - half, x + 6, 32 + half), radius=3 * k, fill="white")
    elif name == "Captions":
        d.rounded_rectangle(g(10, 16, 54, 48), radius=7 * k, outline="white", width=w)
        d.rounded_rectangle(g(18, 34, 46, 39), radius=2.5 * k, fill="white")
        d.rounded_rectangle(g(18, 25, 36, 30), radius=2.5 * k, fill="white")
    elif name == "Reading":  # a text cursor
        d.line([g(32, 14), g(32, 50)], fill="white", width=w)
        d.line([g(22, 14), g(42, 14)], fill="white", width=w)
        d.line([g(22, 50), g(42, 50)], fill="white", width=w)
    elif name == "Summarize":  # a large and a small four-pointed sparkle
        for cx, cy, r in ((27, 34, 17), (46, 17, 8)):
            d.polygon([g(cx + dx, cy + dy) for dx, dy in ((0, -r), (r * 0.28, -r * 0.28), (r, 0), (r * 0.28, r * 0.28),
                                                          (0, r), (-r * 0.28, r * 0.28), (-r, 0),
                                                          (-r * 0.28, -r * 0.28))], fill="white")
    elif name == "Meetings":
        # a person: head over rounded shoulders, legible at 20 px where two figures blur into dots
        d.ellipse(g(23, 12, 41, 30), fill="white")
        d.chord(g(13, 33, 51, 71), 180, 360, fill="white")
    elif name == "Speech to Text":  # a microphone
        d.rounded_rectangle(g(24, 10, 40, 38), radius=8 * k, fill="white")
        d.arc(g(16, 22, 48, 46), 0, 180, fill="white", width=w)
        d.line([g(32, 46), g(32, 54)], fill="white", width=w)
    else:  # a gear: eight teeth around a ring
        for i in range(8):
            a, s = math.radians(i * 45), math.radians(11)
            d.polygon([g(32 + r * math.cos(a + t), 32 + r * math.sin(a + t))
                       for r, t in ((14, -s * 1.6), (24, -s), (24, s), (14, s * 1.6))], fill="white")
        d.ellipse(g(14, 14, 50, 50), fill="white")
        d.ellipse(g(25, 25, 39, 39), fill=PAGE_COLORS["General"])
    return img.resize((size, size), Image.LANCZOS)


def badged(image):
    """The sidebar mark with an orange dot, for a page that needs attention."""
    image = image.copy()
    size = image.width
    ImageDraw.Draw(image).ellipse((size * 0.62, 0, size - 1, size * 0.38), fill="#f7630c", outline="white")
    return image


def recording_image(size):
    """The app icon with a red recording indicator."""
    image = icon_image(size)
    ImageDraw.Draw(image).ellipse((size * 0.62, 0, size - 1, size * 0.38), fill="#e81123", outline="white")
    return image
