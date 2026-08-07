#!/usr/bin/env python3
"""Worker script: reads image bytes from stdin, analyzes face, outputs JSON to stdout. Then exits."""
import sys, json, io, math

def calc_dist(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)

def main():
    image_bytes = sys.stdin.buffer.read()

    import cv2
    import numpy as np
    import mediapipe as mp
    from PIL import Image, ImageDraw

    face_mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=1, refine_landmarks=True, min_detection_confidence=0.5)

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        print(json.dumps({"error": "Не удалось прочитать изображение"}))
        return
    h, w, _ = img.shape
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(img_rgb)
    if not results.multi_face_landmarks:
        print(json.dumps({"error": "Лицо не обнаружено. Отправь чёткое анфас-фото."}))
        return
    landmarks = results.multi_face_landmarks[0]
    points = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks.landmark]

    nose_center = points[1]
    chin = points[152]
    bizygomatic_width = calc_dist(points[234], points[454])
    bigonial_width = calc_dist(points[58], points[288])
    ipd = calc_dist(points[468], points[473])
    nose_width = calc_dist(points[98], points[327])
    mouth_width = calc_dist(points[61], points[291])

    sym_pairs = [(33, 263), (133, 362), (234, 454), (58, 288), (98, 327), (61, 291), (105, 334)]
    deviations = []
    nose_x, _ = nose_center
    for li, ri in sym_pairs:
        lx, _ = points[li]
        rx, _ = points[ri]
        dl = abs(lx - nose_x)
        dr = abs(rx - nose_x)
        if max(dl, dr) > 0:
            deviations.append(abs(dl - dr) / max(dl, dr))
    symmetry_score = 1 - np.mean(deviations) if deviations else 0

    ipd_score = (1 - min(abs(ipd / bizygomatic_width - 0.46) / 0.1, 1.0)) if bizygomatic_width > 0 else 0.5
    nose_width_score = max(0.1, float(np.exp(-((nose_width / ipd - 1.0) ** 2) / (2 * 0.09)))) if ipd > 0 else 0.5
    mouth_score = (1 - min(abs(mouth_width / bizygomatic_width - 0.4) / 0.1, 1.0)) if bizygomatic_width > 0 else 0.5
    jaw_score = (1 - min(abs(bigonial_width / bizygomatic_width - 0.88) / 0.88, 1.0)) if bizygomatic_width > 0 else 0.5

    eye_center_y = (points[159][1] + points[386][1]) / 2
    upper = points[13][1] - eye_center_y
    lower = chin[1] - points[13][1]
    gold_vert_score = (1 - min(abs((upper / lower) - 1.618) / 1.618, 1.0)) if lower > 0 else 0.5

    eye_w = (calc_dist(points[33], points[133]) + calc_dist(points[362], points[263])) / 2
    eye_d = calc_dist(points[133], points[362])
    gold_horiz_score = (1 - min(abs(eye_w / eye_d - 1.0), 1.0)) if eye_d > 0 else 0.5

    left_outer = np.array(points[33]); left_inner = np.array(points[133])
    left_tilt = np.degrees(np.arctan2(*(left_outer - left_inner)[::-1]))
    right_inner = np.array(points[362]); right_outer = np.array(points[263])
    right_tilt = np.degrees(np.arctan2(*(right_outer - right_inner)[::-1]) * np.array([-1, 1]))
    tilt = round((left_tilt + right_tilt) / 2, 1)
    tilt_score = max(0, 1 - abs(tilt - 6.0) / 10)

    psl_raw = (symmetry_score*0.25 + ipd_score*0.15 + nose_width_score*0.1 + mouth_score*0.1 + jaw_score*0.15 + gold_vert_score*0.1 + gold_horiz_score*0.05 + tilt_score*0.1) * 7 + 1
    psl = round(min(max(psl_raw, 1.0), 8.0), 1)

    tips = []
    if symmetry_score < 0.75: tips.append("🔹 Асимметрия: сон на спине, упражнения для лица, ортодонт.")
    if ipd_score < 0.6: tips.append("🔹 Межглазное расстояние: подбери форму бровей, макияж глаз.")
    if nose_width_score < 0.6: tips.append("🔹 Ширина носа: контуринг, коррекция бровей.")
    if mouth_score < 0.6: tips.append("🔹 Ширина рта: макияж губ, форма усов/бороды.")
    if jaw_score < 0.7: tips.append("🔹 Челюсть: mewing, жёсткая пища, ортодонт.")
    if gold_vert_score < 0.6: tips.append("🔹 Вертикальные пропорции: причёска/борода.")
    if gold_horiz_score < 0.5: tips.append("🔹 Расстояние между глазами: форма бровей.")
    if not tips: tips.append("✅ Гармоничные черты, так держать!")

    img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)
    for color, pairs in [
        ((0,255,0), [(10,1), (1,152)]), ((255,255,0), [(234,454)]), ((0,255,255), [(58,288)]),
        ((255,0,255), [(468,473)]), ((128,0,128), [(98,327)]), ((255,0,0), [(61,291)]),
        ((0,128,128), [(168,1)]), ((255,128,0), [(33,133), (362,263)])
    ]:
        for a, b in pairs:
            draw.line([points[a], points[b]], fill=color, width=2)
    buf = io.BytesIO()
    img_pil.save(buf, format='JPEG', quality=90)
    annotated_b64 = __import__('base64').b64encode(buf.getvalue()).decode()

    print(json.dumps({
        "psl": psl, "symmetry": round(symmetry_score * 100),
        "ipd_score": round(ipd_score * 100), "nose_width_score": round(nose_width_score * 100),
        "mouth_score": round(mouth_score * 100), "jaw": round(jaw_score * 100),
        "gold_vert": round(gold_vert_score * 100), "gold_horiz": round(gold_horiz_score * 100),
        "canthal_tilt": tilt, "tilt_score": round(tilt_score * 100), "tips": tips,
        "annotated_b64": annotated_b64,
        "raw_scores": {"symmetry": symmetry_score, "ipd": ipd_score, "nose": nose_width_score,
                       "mouth": mouth_score, "jaw": jaw_score, "gold_vert": gold_vert_score,
                       "gold_horiz": gold_horiz_score, "tilt": tilt_score}
    }))

if __name__ == "__main__":
    main()
