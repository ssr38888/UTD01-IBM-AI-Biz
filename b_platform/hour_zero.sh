#!/bin/sh
# Platform proof for the video: run on the LinuxONE VM and screenshot the output.
# Writes the same text to b_platform/artifacts/hour_zero.txt.
mkdir -p "$(dirname "$0")/artifacts"
{
  echo "== machine (3931/3932 = z16, 9175 = z17, 8561 = z15 has no AI chip)"
  grep -E '^(Type|Model)' /proc/sysinfo 2>/dev/null || uname -m
  grep -o 'nnpa' /proc/cpuinfo | head -1 || echo "nnpa flag not listed in /proc/cpuinfo"
  echo "== resources"; free -h; nproc; df -h . | tail -1
  echo "== torch on the Telum chip"
  TORCH_NNPA_DEBUG=1 ZDNN_ENABLE_PRECHECK=true python3 -c \
    "import torch, torch_nnpa; print(torch.__version__, torch.randn(4, 8).to('nnpa').device)" 2>&1 | tail -5
  echo "== granite"; python3 "$(dirname "$0")/../c_product/granite.py" 2>&1 | tail -1
} 2>&1 | tee "$(dirname "$0")/artifacts/hour_zero.txt"
