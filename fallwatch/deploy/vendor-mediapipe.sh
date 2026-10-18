#!/usr/bin/env sh
# Copies the MediaPipe pose files onto your own server so phones don't need
# to reach jsdelivr / Google Storage. Run on any machine with internet, then
# in phone/app.js set:
#   MP_BUNDLE = "./vendor/vision_bundle.mjs"
#   MP_WASM   = "./vendor/wasm"
#   POSE_MODEL = "./vendor/pose_landmarker_lite.task"
set -eu
V=0.10.14
OUT="$(dirname "$0")/../phone/vendor"
mkdir -p "$OUT/wasm"
base="https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@$V"
curl -fsSL "$base/vision_bundle.mjs" -o "$OUT/vision_bundle.mjs"
for f in vision_wasm_internal.js vision_wasm_internal.wasm vision_wasm_nosimd_internal.js vision_wasm_nosimd_internal.wasm; do
  curl -fsSL "$base/wasm/$f" -o "$OUT/wasm/$f"
done
curl -fsSL "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task" \
  -o "$OUT/pose_landmarker_lite.task"
echo "Saved to $OUT"
