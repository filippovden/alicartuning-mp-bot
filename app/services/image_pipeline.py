"""Пайплайн обработки изображений (раздел 11, V2 ТЗ): удаление фона, приведение к
фирменному шаблону и генерация инфографики преимуществ.

Удаление фона использует опциональную тяжёлую зависимость `rembg` (ONNX U^2-Net) —
если пакет не установлен или модель недоступна (нет сети до её источника), пайплайн
логирует предупреждение и возвращает исходное изображение без обработки, чтобы бот
не падал в окружениях без этой зависимости. Установка: `pip install -r
requirements-images.txt`.
"""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

WB_CANVAS = (900, 1200)
OZON_CANVAS = (700, 933)
# Берём больший из двух допустимых форматов — подходит под оба маркетплейса (раздел 10 ТЗ).
DEFAULT_CANVAS = (1200, 1600)

INFOGRAPHIC_SIZE = (1200, 1200)

BRAND_BG_COLOR = (255, 255, 255)
BRAND_ACCENT_COLOR = (20, 20, 20)
BRAND_TEXT_COLOR = (30, 30, 30)

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


class ImageProcessingError(Exception):
    pass


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    logger.warning("Не найден TTF-шрифт (%s) — использую встроенный растровый шрифт PIL", _FONT_CANDIDATES)
    return ImageFont.load_default()


def remove_background(image_bytes: bytes) -> bytes:
    """Удаляет фон через rembg (U^2-Net). При недоступности пакета/модели — no-op."""
    try:
        from rembg import remove as rembg_remove
    except ImportError:
        logger.warning("Пакет rembg не установлен — удаление фона пропущено (см. requirements-images.txt)")
        return image_bytes

    try:
        return rembg_remove(image_bytes)
    except Exception as exc:  # модель может быть недоступна для скачивания в изолированной сети
        logger.warning("Удаление фона не удалось (%s) — использую исходное изображение", exc)
        return image_bytes


def compose_on_brand_background(
    image_bytes: bytes,
    canvas_size: tuple[int, int] = DEFAULT_CANVAS,
    background_color: tuple[int, int, int] = BRAND_BG_COLOR,
) -> bytes:
    """Вписывает товар (после удаления фона) в канвас нужного размера на едином фоне
    (раздел 11 ТЗ, п. «Приведение к шаблону»)."""
    try:
        foreground = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise ImageProcessingError(f"Не удалось открыть изображение: {exc}") from exc

    if foreground.mode not in ("RGBA", "LA"):
        foreground = foreground.convert("RGBA")

    canvas = Image.new("RGBA", canvas_size, background_color + (255,))

    fg_w, fg_h = foreground.size
    scale = min(canvas_size[0] / fg_w, canvas_size[1] / fg_h) * 0.85  # 85% — оставляем поля
    new_size = (max(1, int(fg_w * scale)), max(1, int(fg_h * scale)))
    foreground = foreground.resize(new_size, Image.LANCZOS)

    offset = ((canvas_size[0] - new_size[0]) // 2, (canvas_size[1] - new_size[1]) // 2)
    canvas.paste(foreground, offset, foreground)

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def process_product_photo(image_bytes: bytes, canvas_size: tuple[int, int] = DEFAULT_CANVAS) -> bytes:
    """Полный цикл: удаление фона + приведение к фирменному шаблону."""
    no_bg = remove_background(image_bytes)
    return compose_on_brand_background(no_bg, canvas_size=canvas_size)


def generate_infographic(
    bullets: list[str],
    title: str | None = None,
    size: tuple[int, int] = INFOGRAPHIC_SIZE,
) -> bytes:
    """Генерирует инфографику преимуществ: заголовок + список буллетов с иконками
    (раздел 11, 12 ТЗ). MVP-версия — текстовые слои на фирменном фоне без внешних
    иконок (подход «слоёв», как описано в ТЗ)."""
    canvas = Image.new("RGB", size, BRAND_BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    margin = int(size[0] * 0.08)
    y = margin

    if title:
        title_font = _load_font(int(size[1] * 0.045))
        draw.text((margin, y), title, fill=BRAND_ACCENT_COLOR, font=title_font)
        y += int(size[1] * 0.09)
        draw.line([(margin, y), (size[0] - margin, y)], fill=BRAND_ACCENT_COLOR, width=3)
        y += int(size[1] * 0.05)

    bullet_font = _load_font(int(size[1] * 0.035))
    line_height = int(size[1] * 0.12)

    for bullet in bullets[:4]:
        _draw_bullet(draw, margin, y, bullet, bullet_font, size[0] - 2 * margin)
        y += line_height

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _draw_bullet(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font, max_width: int) -> None:
    badge_radius = 18
    draw.ellipse([(x, y), (x + 2 * badge_radius, y + 2 * badge_radius)], fill=BRAND_ACCENT_COLOR)
    draw.text((x + badge_radius - 6, y + badge_radius - 12), "✓", fill=BRAND_BG_COLOR, font=font)

    text_x = x + 2 * badge_radius + 16
    wrapped = _wrap_text(text, font, max_width - (2 * badge_radius + 16))
    for i, line in enumerate(wrapped[:2]):
        draw.text((text_x, y + i * int(font.size * 1.3)), line, fill=BRAND_TEXT_COLOR, font=font)


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = font.getbbox(candidate)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
