#!/bin/bash
# ============================================================
# 3DGS 完整流水线：视频 -> COLMAP 位姿 -> Splatfacto 训练 -> 预览
#
# 用法:
#   bash pipeline.sh <video> [名称] [迭代数] [抽帧FPS]
#
# 示例:
#   bash pipeline.sh data/desk.mp4 desk
#   bash pipeline.sh data/desk.mp4 desk 30000 8
# ============================================================
set -euo pipefail

VIDEO="${1:?用法: bash pipeline.sh <video> [名称] [迭代数] [FPS]}"
NAME="${2:-$(basename "$VIDEO" | sed 's/\.[^.]*$//')}"
ITERS="${3:-20000}"
FPS="${4:-6}"

ROOT=/mnt/d/job/3DGS
DATA=$ROOT/data/$NAME
OUT=$ROOT/outputs/$NAME
PREVIEW=$ROOT/renders/$NAME
LOG=$ROOT/logs/$NAME.log
PY=$HOME/envs/nerfstudio/bin

# ---------- 环境 ----------
# gsplat 编译需要 gcc-12（系统 gcc-15 对 CUDA 12.1 太新）
SHIM=$HOME/cuda12shim
mkdir -p $SHIM
ln -sf /usr/bin/gcc-12 $SHIM/gcc  2>/dev/null || true
ln -sf /usr/bin/g++-12 $SHIM/g++  2>/dev/null || true
ln -sf /usr/bin/gcc-12 $SHIM/cc   2>/dev/null || true
ln -sf /usr/bin/g++-12 $SHIM/c++  2>/dev/null || true
export PATH=$SHIM:$PY:/usr/bin:/bin:/usr/sbin
export CUDA_HOME=/usr
export MAX_JOBS=2          # 内存仅 7.4GB，>2 会 OOM 被 kill(code 137)

mkdir -p "$ROOT/logs" "$ROOT/outputs" "$ROOT/renders"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# ---------- 0. 输入检查 ----------
[ -f "$VIDEO" ] || { echo "❌ 找不到视频: $VIDEO"; exit 1; }
log "输入: $VIDEO"
log "名称: $NAME   迭代: $ITERS   抽帧: ${FPS}fps"
echo "" > "$LOG"

DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$VIDEO")
log "视频时长: ${DUR}s  ->  预计 $(${PY}/python -c "print(int(float('$DUR')*$FPS))") 帧"

# ---------- 1. gsplat CUDA 扩展 ----------
log "检查 gsplat CUDA 扩展..."
if ! $PY/python -c "from gsplat.cuda._backend import _C; assert _C is not None" 2>/dev/null; then
    log "⚠️  gsplat 未编译，开始 JIT 编译（约 10 分钟，仅首次）..."
    $PY/python -c "from gsplat.cuda._backend import _C; print('ok:', _C)" >> "$LOG" 2>&1
fi
log "✅ gsplat 就绪"

# ---------- 2. 抽帧 ----------
log "抽帧 @ ${FPS}fps ..."
rm -rf "$DATA"
mkdir -p "$DATA/src"
ffmpeg -y -loglevel error -i "$VIDEO" -vf "fps=$FPS" -q:v 2 "$DATA/src/%06d.jpg" >> "$LOG" 2>&1
NFRAMES=$(ls "$DATA/src" | wc -l)
log "✅ 抽出 $NFRAMES 帧"

# ---------- 3. COLMAP 位姿求解 ----------
# pinhole  : 避免 OPENCV 8参数在初始2视图BA中发散（关键修复）
# sequential: 视频序列正确算法，比 exhaustive 快一个数量级
log "COLMAP: 特征提取 + sequential 匹配 + 稀疏重建..."
$PY/ns-process-data images \
    --data "$DATA/src" \
    --output-dir "$DATA" \
    --camera-type pinhole \
    --matching-method sequential \
    --num-downscales 1 >> "$LOG" 2>&1

if [ ! -f "$DATA/transforms.json" ]; then
    log "❌ transforms.json 生成失败，查看 $LOG"
    exit 1
fi

# ---------- 4. 配准率检查 ----------
REG=$($PY/python - "$DATA" <<'EOF'
import json, sys, os
d = sys.argv[1]
n = len(json.load(open(os.path.join(d, "transforms.json")))["frames"])
src = len(os.listdir(os.path.join(d, "src")))
print(f"{n} {src}")
EOF
)
NREG=$(echo $REG | cut -d' ' -f1)
NSRC=$(echo $REG | cut -d' ' -f2)
RATE=$((NREG * 100 / NSRC))
log "配准: $NREG / $NSRC  ($RATE%)"

if [ "$RATE" -lt 90 ]; then
    log "⚠️  配准率偏低。常见原因：移动速度过快 / 场景有移动物体 / 自动曝光 / 纹理不足"
    log "    建议：降低移动速度至 0.3-0.5m/s、锁定曝光、关闭电子防抖、增加场景纹理"
fi

# ---------- 5. 训练 ----------
log "训练 splatfacto: $ITERS 迭代..."
rm -rf "$OUT"
$PY/ns-train splatfacto \
    --data "$DATA" \
    --output-dir "$OUT" \
    --max-num-iterations "$ITERS" \
    --vis viewer \
    --viewer.num-rays-per-chunk 4096 >> "$LOG" 2>&1 &

TRAIN_PID=$!
log "训练已启动 (PID=$TRAIN_PID)，viewer 将在 http://localhost:7007"

# 等待训练完成（ns-train 训练后会保持运行以提供 viewer）
for i in $(seq 1 200); do
    if grep -aq "Training Finished" "$LOG" 2>/dev/null; then
        log "✅ 训练完成"
        break
    fi
    if grep -aq "Traceback\|CUDA out of memory" "$LOG" 2>/dev/null; then
        log "❌ 训练出错，查看 $LOG"
        exit 1
    fi
    sleep 15
done

# ---------- 6. 预览渲染 ----------
CFG=$(find "$OUT" -name config.yml | head -1)
if [ -n "$CFG" ]; then
    log "渲染预览轨迹..."
    rm -rf "$PREVIEW"; mkdir -p "$PREVIEW"
    $PY/ns-render interpolate \
        --load-config "$CFG" \
        --output-format video \
        --output-path "$PREVIEW/preview.mp4" \
        --pose-source train \
        --interpolation-steps 10 \
        --frame-rate 6 >> "$LOG" 2>&1
    log "✅ 预览: $PREVIEW/preview.mp4"
fi

# ---------- 7. 汇总 ----------
echo ""
echo "══════════════════════════════════════════════"
echo "✅ 完成: $NAME"
echo "──────────────────────────────────────────────"
echo "  数据集   $DATA"
echo "  模型     $(find "$OUT" -name '*.ckpt' | head -1)"
echo "  预览     $PREVIEW/preview.mp4"
echo "  Viewer   http://localhost:7007"
echo "══════════════════════════════════════════════"
