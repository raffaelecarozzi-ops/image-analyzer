from flask import Flask, request, jsonify
from PIL import Image
import requests
from io import BytesIO

app = Flask(__name__)

def is_near_white(pixel, threshold=245):
    r, g, b = pixel[:3]
    return r >= threshold and g >= threshold and b >= threshold

def get_border_white_ratio(img):
    width, height = img.size
    pixels = []
    step = max(1, min(width, height) // 120)

    for x in range(0, width, step):
        pixels.append(img.getpixel((x, 0)))
        pixels.append(img.getpixel((x, height - 1)))

    for y in range(0, height, step):
        pixels.append(img.getpixel((0, y)))
        pixels.append(img.getpixel((width - 1, y)))

    white_count = sum(1 for p in pixels if is_near_white(p, 245))
    return white_count / max(1, len(pixels))

def get_content_box_metrics(img):
    width, height = img.size
    coords = []

    step_x = max(1, width // 160)
    step_y = max(1, height // 160)

    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            pixel = img.getpixel((x, y))
            if not is_near_white(pixel, 242):
                coords.append((x, y))

    if not coords:
        return {
            "has_content": False,
            "content_ratio": 0.0,
            "aspect_ratio": 1.0
        }

    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]

    box_w = max(xs) - min(xs)
    box_h = max(ys) - min(ys)

    content_ratio = (box_w * box_h) / (width * height)
    aspect_ratio = box_w / max(1, box_h)

    return {
        "has_content": True,
        "content_ratio": content_ratio,
        "aspect_ratio": aspect_ratio
    }

def analyze_image(image_url):
    try:
        response = requests.get(image_url, timeout=20)
        if response.status_code != 200:
            return {
                "ok": False,
                "status": "red",
                "reason": f"http_{response.status_code}"
            }

        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            return {
                "ok": False,
                "status": "red",
                "reason": "not_image"
            }

        img = Image.open(BytesIO(response.content)).convert("RGB")

        border_white_ratio = get_border_white_ratio(img)
        metrics = get_content_box_metrics(img)

        # Fondo diverso da bianco => giallo
        if border_white_ratio < 0.82:
            return {
                "ok": True,
                "status": "yellow",
                "reason": "background_not_white",
                "border_white_ratio": round(border_white_ratio, 4)
            }

        # Immagine praticamente vuota ma con sfondo bianco
        if not metrics["has_content"]:
            return {
                "ok": True,
                "status": "green",
                "reason": "white_background_empty"
            }

        content_ratio = metrics["content_ratio"]
        aspect_ratio = metrics["aspect_ratio"]

        # Contenuto chiaramente troppo piccolo
        if content_ratio < 0.10:
            return {
                "ok": True,
                "status": "yellow",
                "reason": "content_clearly_too_small",
                "content_ratio": round(content_ratio, 4)
            }

        # Contenuto chiaramente troppo rettangolare
        if aspect_ratio < 0.35 or aspect_ratio > 2.8:
            return {
                "ok": True,
                "status": "yellow",
                "reason": "content_clearly_too_rectangular",
                "aspect_ratio": round(aspect_ratio, 4)
            }

        return {
            "ok": True,
            "status": "green",
            "reason": "white_background_ok",
            "border_white_ratio": round(border_white_ratio, 4),
            "content_ratio": round(content_ratio, 4),
            "aspect_ratio": round(aspect_ratio, 4)
        }

    except Exception as e:
        return {
            "ok": False,
            "status": "red",
            "reason": str(e)
        }

@app.route("/", methods=["GET"])
def health():
    return jsonify({"ok": True, "message": "Analyzer online"}), 200

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    image_url = str(data.get("image_url", "")).strip()

    if not image_url:
        return jsonify({
            "ok": False,
            "status": "red",
            "reason": "missing_url"
        }), 400

    result = analyze_image(image_url)
    return jsonify(result), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
