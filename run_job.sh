#!/bin/bash
# ============================================================
# 由 app.py 调用的单任务执行器（WSL 端）
# 用法: bash run_job.sh <名称> <视频WSL路径> <迭代数> <抽帧FPS>
# 状态写入 logs/<名称>.status:  "阶段|详情"
# 训练进度写入 logs/<名称>.progress: 单个浮点百分比 0-100
# ============================================================
set -uo pipefail

NAME="$1"
VIDEO="$2"
ITERS="${3:-20000}"
FPS="${4:-6}"

ROOT=/mnt/d/job/3DGS
DATA=$ROOT/data/$NAME
OUT=$ROOT/outputs/$NAME
PREVIEW=$ROOT/renders/$NAME
LOGDIR=$ROOT/logs
LOG=$LOGDIR/$NAME.log
STATUS=$LOGDIR/$NAME.status
PROGRESS=$LOGDIR/$NAME.progress
PY=$HOME/envs/nerfstudio/bin

# gcc shim（gsplat JIT 编译需要 gcc-12）
SHIM=$HOME/cuda12shim
mkdir -p $SHIM
ln -sf /usr/bin/gcc-12 $SHIM/gcc  2>/dev/null
ln -sf /usr/bin/g++-12 $SHIM/g++  2>/dev/null
ln -sf /usr/bin/gcc-12 $SHIM/cc   2>/dev/null
ln -sf /usr/bin/g++-12 $SHIM/c++  2>/dev/null
export PATH=$SHIM:$PY:/usr/bin:/bin:/usr/sbin
export CUDA_HOME=/usr
export MAX_JOBS=2

mkdir -p "$LOGDIR" "$ROOT/outputs" "$ROOT/renders" "$ROOT/web/assets" "$ROOT/data/inbox"

say()  { echo "$1|$2" > "$STATUS"; }
prog() { echo "$1" > "$PROGRESS"; }
fail() { echo "error|$1" > "$STATUS"; prog 0; exit 1; }

: > "$LOG"
prog 0

[ -f "$VIDEO" ] || fail "找不到视频: $VIDEO"

# ---------- 0. gsplat CUDA 扩展 ----------
say gsplat "检查 CUDA 扩展"
if ! $PY/python -c "from gsplat.cuda._backend import _C; assert _C is not None" 2>/dev/null; then
    say gsplat "首次编译 gsplat，约 10 分钟"
    $PY/python -c "from gsplat.cuda._backend import _C; print('ok')" >> "$LOG" 2>&1 || fail "gsplat 编译失败，见 $LOG"
fi
say gsplat "就绪"

# ---------- 1. 抽帧 ----------
say extract "ffmpeg 抽帧 @ ${FPS}fps"
rm -rf "$DATA"
mkdir -p "$DATA/src"
ffmpeg -y -loglevel error -i "$VIDEO" -vf "fps=$FPS" -q:v 2 "$DATA/src/%06d.jpg" >> "$LOG" 2>&1 \
    || fail "ffmpeg 抽帧失败，见 $LOG"
NFRAMES=$(ls "$DATA/src" | wc -l)
[ "$NFRAMES" -gt 4 ] || fail "只抽出 $NFRAMES 帧，视频太短或损坏"
prog 3

# ---------- 2. COLMAP 位姿 ----------
say colmap "特征匹配 + 稀疏重建（约 1-5 分钟）"
$PY/ns-process-data images \
    --data "$DATA/src" \
    --output-dir "$DATA" \
    --camera-type pinhole \
    --matching-method sequential \
    --num-downscales 1 >> "$LOG" 2>&1
[ -f "$DATA/transforms.json" ] || fail "COLMAP 位姿求解失败，见 $LOG"

NREG=$($PY/python -c "import json;print(len(json.load(open('$DATA/transforms.json'))['frames']))")
RATE=$((NREG * 100 / NFRAMES))
prog 28
if [ "$RATE" -lt 90 ]; then
    say colmap "配准 $NREG/$NFRAMES ($RATE%)，偏低但继续训练"
else
    say colmap "配准 $NREG/$NFRAMES ($RATE%)"
fi

# ---------- 3. 训练 ----------
say train "训练中 0%"
rm -rf "$OUT"
(   # 子 shell 里边训边解析进度写 progress 文件
    $PY/ns-train splatfacto \
        --data "$DATA" \
        --output-dir "$OUT" \
        --max-num-iterations "$ITERS" \
        --vis viewer \
        --viewer.quit-on-train-completion True \
        --viewer.num-rays-per-chunk 4096 2>&1 | tee -a "$LOG" | while IFS= read -r line; do
            pct=$(printf '%s' "$line" | grep -oE '[0-9]+ \([0-9]+\.[0-9]+%\)' | tail -1 | grep -oE '[0-9]+\.[0-9]+')
            if [ -n "$pct" ]; then
                awk -v p="$pct" 'BEGIN{printf "%.1f\n", 28+0.64*p/100}' > "$PROGRESS"
                echo "train|训练中 ${pct}%" > "$STATUS"
            fi
        done
    exit "${PIPESTATUS[0]}"
)
TRAIN_RC=$?
[ "$TRAIN_RC" -eq 0 ] || fail "训练出错 (rc=$TRAIN_RC)，见 $LOG"

CFG=$(find "$OUT" -name config.yml | head -1)
[ -n "$CFG" ] || fail "找不到训练产物 config.yml"
prog 92

# ---------- 4. 导出网页模型 ----------
say export "导出 splat.ply（约 1-3 分钟）"
ASSETDIR=$ROOT/web/assets/$NAME
rm -rf "$ASSETDIR"; mkdir -p "$ASSETDIR"
$PY/ns-export gaussian-splat \
    --load-config "$CFG" \
    --output-dir "$ASSETDIR" \
    --output-filename splat.ply \
    --ply-color-mode rgb >> "$LOG" 2>&1
[ -f "$ASSETDIR/splat.ply" ] || fail "导出 splat.ply 失败，见 $LOG"
prog 97

# ---------- 5. 预览渲染（可选，失败不致命） ----------
say preview "渲染预览轨迹（可选步骤）"
rm -rf "$PREVIEW"; mkdir -p "$PREVIEW"
if $PY/ns-render interpolate \
        --load-config "$CFG" \
        --output-format video \
        --output-path "$PREVIEW/preview.mp4" \
        --pose-source train \
        --interpolation-steps 10 \
        --frame-rate 6 >> "$LOG" 2>&1; then
    :
else
    rm -rf "$PREVIEW"
fi
prog 100

say done "$CFG"
exit 0
