"""Turn UR Fall Detection sequences (or any videos) into pose keypoint files for evaluate.mjs.

Run on a laptop (x86/ARM), not on LinuxONE: MediaPipe's Python package has no s390x build.
The phone runs the same pose model in the browser, so these keypoints match what the phone sees.

    pip install mediapipe opencv-python
    python eval/download_urfall.py            # fetch the sequences (CC BY-NC-SA 4.0)
    python eval/extract_keypoints.py data/urfall data/keypoints
    node eval/evaluate.mjs data/keypoints

Input: a folder of either
  * UR Fall zips/folders: fall-01-cam0-rgb(.zip) ... adl-40-cam0-rgb(.zip)  (PNG frames, 30 fps)
  * videos (.mp4/.avi/.mov); label = "fall" if the file name contains "fall", else "adl"
Output: one JSON per sequence: {name, label, fps, frames: [{t, lms}]}
"""
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_lite/float16/1/pose_landmarker_lite.task")
URFALL_FPS = 30


def model_path(cache: Path) -> str:
    p = cache / "pose_landmarker_lite.task"
    if not p.exists():
        cache.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, p)
    return str(p)


def frames_from_zip(path: Path):
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith(".png")):
            data = z.read(name)
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            yield img


def frames_from_dir(path: Path):
    for p in sorted(path.glob("*.png")):
        yield cv2.imread(str(p))


def frames_from_video(path: Path):
    cap = cv2.VideoCapture(str(path))
    while True:
        ok, img = cap.read()
        if not ok:
            break
        yield img
    cap.release()


def label_of(name: str) -> str:
    return "fall" if "fall" in name.lower() else "adl"


def main(src: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    model = model_path(out / ".model")
    items = sorted([*src.glob("*-cam0-rgb.zip"), *[d for d in src.glob("*-cam0-rgb") if d.is_dir()],
                    *src.glob("*.mp4"), *src.glob("*.avi"), *src.glob("*.mov")])
    if not items:
        sys.exit(f"nothing to read in {src}")
    for item in items:
        name = item.name.split(".")[0]
        if item.suffix == ".zip":
            frames, fps = frames_from_zip(item), URFALL_FPS
        elif item.is_dir():
            frames, fps = frames_from_dir(item), URFALL_FPS
        else:
            fps = cv2.VideoCapture(str(item)).get(cv2.CAP_PROP_FPS) or 30
            frames = frames_from_video(item)

        opts = PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=model),
                                     running_mode=RunningMode.VIDEO, num_poses=1)
        rows, aspect = [], None
        with PoseLandmarker.create_from_options(opts) as lm:
            for i, bgr in enumerate(frames):
                if bgr is None:
                    continue
                if aspect is None:
                    aspect = bgr.shape[1] / bgr.shape[0]
                t = round(i * 1000 / fps)
                img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                res = lm.detect_for_video(img, t)
                lms = None
                if res.pose_landmarks:
                    lms = [{"x": round(p.x, 4), "y": round(p.y, 4), "visibility": round(p.visibility or 0, 3)}
                           for p in res.pose_landmarks[0]]
                rows.append({"t": t, "lms": lms})
        (out / f"{name}.json").write_text(json.dumps(
            {"name": name, "label": label_of(name), "fps": fps, "aspect": aspect, "frames": rows}))
        seen = sum(r["lms"] is not None for r in rows)
        print(f"{name}: {len(rows)} frames, person found in {seen}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
