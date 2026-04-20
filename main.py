from flask import Flask, request, jsonify
from PIL import Image
import requests
from io import BytesIO

app = Flask(__name__)

# =========================
# CONFIG
# =========================
WHITE_THRESHOLD = 245
BORDER_WHITE_MIN = 0.65
CONTENT_RATIO_MIN = 0.045
ASPECT_RATIO_MIN = 0.18
ASPECT_RATIO_MAX = 5.0

# Nuovo controllo per "immagine dentro immagine"
INNER_FRAME_OFFSET_RATIO = 0.10
INNER_FRAME_WHITE_MIN = 0.78


# =========================
# HELPERS
# =========================
def is_near_white(pixel, threshold=WHITE_THRESHOLD):
    r, g, b = pixel[:3]
    return r >= threshold and g >= threshold and b >= threshold


def safe_getpixel(img, x, y):
    width, height = img.size
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    return img.getpixel((x, y))


def get_border_white_ratio(img):
    width, height = img.size
    pixels = []
    step = max(1, min(width, height) // 120)

    for x in range(0, width, step):
        pixels.append(safe_getpixel(img, x, 0))
        pixels.append(safe_getpixel(img, x, height - 1))

    for y in range(0, height, step):
        pixels.append(safe_getpixel(img, 0, y))
        pixels.append(safe_getpixel(img, width - 1, y))

    white_count = sum(1 for p in pixels if is_near_white(p))
    return white_count / max(1, len(pixels))


def get_inner_frame_white_ratio(img, offset_ratio=INNER_FRAME_OFFSET_RATIO):
    """
    Controlla una cornice interna al canvas.
    Serve a intercettare casi in cui dentro il 1080x1080 è stata inserita
    un’altra immagine rettangolare con sfondo non coerente.
    """
    width, height = img.size
    pixels = []

    x1 = int(width * offset_ratio)
    x2 = int(width * (1 - offset_ratio))
    y1 = int(height * offset_ratio)
    y2 = int(height * (1 - offset_ratio))

    step = max(1, min(width, height) // 120)

    # linee orizzontali interne
    for x in range(x1, x2, step):
        pixels.append(safe_getpixel(img, x, y1))
        pixels.append(safe_getpixel(img, x, y2))

    # linee verticali interne
    for y in range(y1, y2, step):
        pixels.append(safe_getpixel(img, x1, y))
        pixels.append(safe_getpixel(img, x2, y))

    white_count = sum(1 for p in pixels if is_near_white(p))
    return white_count / max(1, len(pixels))


def get_content_box_metrics(img):
    width, height = img.size
    coords = []

    step_x = max(1, width // 160)
    step_y = max(1, height // 160)

    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            pixel = img.getpixel((x, y))
            if not is_near_white(pixel):
                coords.append((x, y))

    if not coords:
        return {
            "has_content": False,
            "content_ratio": 0.0,
            "aspect_ratio": 1.0
        }

    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    box_w = max(1, max_x - min_x)
    box_h = max(1, max_y - min_y)

    content_ratio = (box_w * box_h) / (width * height)
    aspect_ratio = box_w / box_h

    return {
        "has_content": True,
        "content_ratio": content_ratio,
        "aspect_ratio": aspect_ratio
    }


def build_response(
    ok,
    status,
    reason,
    border_white_ratio=None,
    inner_frame_white_ratio=None,
    content_ratio=None,
    aspect_ratio=None
):
    return {
        "ok": ok,
        "status": status,
        "reason": reason,
        "border_white_ratio": round(border_white_ratio, 4) if border_white_ratio is not None else None,
        "inner_frame_white_ratio": round(inner_frame_white_ratio, 4) if inner_frame_white_ratio is not None else None,
        "content_ratio": round(content_ratio, 4) if content_ratio is not None else None,
        "aspect_ratio": round(aspect_ratio, 4) if aspect_ratio is not None else None,
        "debug_thresholds": {
            "WHITE_THRESHOLD": WHITE_THRESHOLD,
            "BORDER_WHITE_MIN": BORDER_WHITE_MIN,
            "INNER_FRAME_OFFSET_RATIO": INNER_FRAME_OFFSET_RATIO,
            "INNER_FRAME_WHITE_MIN": INNER_FRAME_WHITE_MIN,
            "CONTENT_RATIO_MIN": CONTENT_RATIO_MIN,
            "ASPECT_RATIO_MIN": ASPECT_RATIO_MIN,
            "ASPECT_RATIO_MAX": ASPECT_RATIO_MAX
        }
    }


# =========================
# ANALYSIS
# =========================
def analyze_image(image_url):
    try:
        response = requests.get(image_url, timeout=20)

        if response.status_code != 200:
            return build_response(False, "red", f"http_{response.status_code}")

        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            return build_response(False, "red", "not_image")

        img = Image.open(BytesIO(response.content)).convert("RGBA")

        # Appoggia eventuale trasparenza su fondo bianco
        white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(white_bg, img).convert("RGB")

        border_white_ratio = get_border_white_ratio(img)
        inner_frame_white_ratio = get_inner_frame_white_ratio(img)
        metrics = get_content_box_metrics(img)

        content_ratio = metrics["content_ratio"]
        aspect_ratio = metrics["aspect_ratio"]

        # 1) Sfondo esterno non abbastanza bianco
        if border_white_ratio < BORDER_WHITE_MIN:
            return build_response(
                True,
                "yellow",
                "background_not_white",
                border_white_ratio,
                inner_frame_white_ratio,
                content_ratio,
                aspect_ratio
            )

        # 2) Caso "immagine dentro il canvas"
        # bordo esterno passa, ma la cornice interna rivela un rettangolo non armonico
        if inner_frame_white_ratio < INNER_FRAME_WHITE_MIN:
            return build_response(
                True,
                "yellow",
                "inset_canvas_detected",
                border_white_ratio,
                inner_frame_white_ratio,
                content_ratio,
                aspect_ratio
            )

        # 3) Nessun contenuto rilevato
        if not metrics["has_content"]:
            return build_response(
                True,
                "green",
                "white_background_empty",
                border_white_ratio,
                inner_frame_white_ratio,
                content_ratio,
                aspect_ratio
            )

        # 4) Contenuto troppo piccolo
        if content_ratio < CONTENT_RATIO_MIN:
            return build_response(
                True,
                "yellow",
                "content_clearly_too_small",
                border_white_ratio,
                inner_frame_white_ratio,
                content_ratio,
                aspect_ratio
            )

        # 5) Contenuto troppo rettangolare
        if aspect_ratio < ASPECT_RATIO_MIN or aspect_ratio > ASPECT_RATIO_MAX:
            return build_response(
                True,
                "yellow",
                "content_clearly_too_rectangular",
                border_white_ratio,
                inner_frame_white_ratio,
                content_ratio,
                aspect_ratio
            )

        # 6) Tutto ok
        return build_response(
            True,
            "green",
            "white_background_ok",
            border_white_ratio,
            inner_frame_white_ratio,
            content_ratio,
            aspect_ratio
        )

    except Exception as e:
        return build_response(False, "red", str(e))


# =========================
# ROUTES
# =========================
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "message": "Analyzer online",
        "version": "final-white245-innerframe",
        "thresholds": {
            "WHITE_THRESHOLD": WHITE_THRESHOLD,
            "BORDER_WHITE_MIN": BORDER_WHITE_MIN,
            "INNER_FRAME_OFFSET_RATIO": INNER_FRAME_OFFSET_RATIO,
            "INNER_FRAME_WHITE_MIN": INNER_FRAME_WHITE_MIN,
            "CONTENT_RATIO_MIN": CONTENT_RATIO_MIN,
            "ASPECT_RATIO_MIN": ASPECT_RATIO_MIN,
            "ASPECT_RATIO_MAX": ASPECT_RATIO_MAX
        }
    }), 200


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    image_url = str(data.get("image_url", "")).strip()

    if not image_url:
        return jsonify(build_response(False, "red", "missing_url")), 400

    result = analyze_image(image_url)
    return jsonify(result), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
