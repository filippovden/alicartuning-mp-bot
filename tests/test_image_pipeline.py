import io

from PIL import Image

from app.services.image_pipeline import (
    DEFAULT_CANVAS,
    compose_on_brand_background,
    generate_infographic,
    process_product_photo,
    remove_background,
)


def _sample_jpeg(size=(400, 600), color=(120, 80, 40)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_remove_background_without_rembg_is_noop():
    raw = _sample_jpeg()
    result = remove_background(raw)
    assert result == raw


def test_compose_on_brand_background_produces_canvas_size():
    raw = _sample_jpeg()
    composed = compose_on_brand_background(raw)
    img = Image.open(io.BytesIO(composed))
    assert img.size == DEFAULT_CANVAS
    assert img.mode == "RGB"


def test_compose_on_brand_background_custom_canvas():
    raw = _sample_jpeg()
    composed = compose_on_brand_background(raw, canvas_size=(900, 1200))
    img = Image.open(io.BytesIO(composed))
    assert img.size == (900, 1200)


def test_process_product_photo_end_to_end():
    raw = _sample_jpeg()
    result = process_product_photo(raw)
    img = Image.open(io.BytesIO(result))
    img.verify()


def test_infographic_canvas_is_3_by_4():
    """Раздел 4 ТЗ: карточка маркетплейса — вертикаль 900×1200 (3:4), не квадрат."""
    raw = generate_infographic(["ABS-пластик", "Для Lada Vesta", "Чёрный глянец"], title="ALICARTUNING")
    assert raw.startswith(b"\x89PNG")
    im = Image.open(io.BytesIO(raw))
    assert im.size == (900, 1200)


def test_generate_infographic_returns_valid_png():
    bullets = [
        "Прочный ABS-пластик (не трескается зимой)",
        "Лёгкий монтаж без сверления",
        "Агрессивный стиль BMW-M",
    ]
    result = generate_infographic(bullets, title="ALICARTUNING")
    img = Image.open(io.BytesIO(result))
    assert img.format == "PNG"
    img.verify()


def test_generate_infographic_handles_empty_bullets():
    result = generate_infographic([], title="ALICARTUNING")
    img = Image.open(io.BytesIO(result))
    img.verify()


def test_generate_infographic_wraps_long_text():
    long_bullet = "Очень длинное описание преимущества которое точно не поместится в одну строку и должно перенестись"
    result = generate_infographic([long_bullet])
    img = Image.open(io.BytesIO(result))
    img.verify()
