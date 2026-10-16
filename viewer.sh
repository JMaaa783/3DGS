#!/bin/bash
# 官方 nerfstudio viewer 的启停（由 app.py 调用）
#   bash viewer.sh start <config.yml> [端口]
#   bash viewer.sh stop
set -u
PY=$HOME/envs/nerfstudio/bin
LOGDIR=/mnt/d/job/3DGS/logs

SHIM=$HOME/cuda12shim
export PATH=$SHIM:$PY:/usr/bin:/bin:/usr/sbin
export CUDA_HOME=/usr

case "$1" in
  start)
    CFG="${2:?需要 config.yml 路径}"
    PORT="${3:-7007}"
    pkill -f "ns-viewer" 2>/dev/null
    sleep 1
    # 关键：必须前台 exec，让 wsl.exe 的存活期 == viewer 的存活期。
    # 之前用 `setsid nohup ... &` 后脚本立即退出，wsl.exe 随之结束，
    # WSL 会把会话里残留的后台进程一起收走，于是 viewer 秒死、日志为空。
    # 调用方（app.py）用 Popen 启动本脚本且不等待，即可长期保活。
    exec $PY/ns-viewer \
        --load-config "$CFG" \
        --viewer.websocket-port "$PORT"
    ;;
  stop)
    pkill -f "ns-viewer" 2>/dev/null
    echo "stopped"
    ;;
esac
