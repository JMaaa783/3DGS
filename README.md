# 3DGS 视频重建流水线：视频 → 相机位姿 → 高斯泼溅 → 浏览器漫游

在 **Windows + WSL2 (Ubuntu) + RTX 4060 8GB** 上跑通的完整链路。
支持网页上传视频自动处理，训练完成后可在浏览器内直接漫游。

---

## 特性

- **网页控制台**：上传视频 → 自动抽帧 → COLMAP 求位姿 → 训练 → 浏览器漫游，全程实时进度
- **两种漫游方式**：nerfstudio 官方 Viewer（自由飞行）+ 自写 WebGL2 播放器（零依赖，离线可用）
- **零 pip 依赖**：控制台是纯 Python 标准库实现，双击即可启动
- **历史模型自动导入**：启动时扫描 `outputs*/` 下的 `config.yml`，已训练的模型可直接打开
- **按需导出**：PLY 缺失时在网页点一下即可生成，不必重训

---

## 快速开始

### 方式一：网页控制台（推荐）

双击 `start_console.bat`，或在 PowerShell 中：

```powershell
cd D:\job\3DGS
python app.py
```

浏览器会自动打开 **http://127.0.0.1:8000**。在页面上：

1. 选择视频文件上传（或直接放进 `data/` 投料）
2. 填任务名、迭代数（默认 20000）、抽帧 FPS（默认 6）
3. 点「开始训练」，页面实时显示阶段进度
4. 完成后点「网页播放器」直接漫游，或点「官方 Viewer」进入 7007 端口

> **两个端口**：控制台 `8000`，官方 Viewer `7007`

### 方式二：WSL 命令行

```bash
bash /mnt/d/job/3DGS/pipeline.sh /mnt/d/job/3DGS/data/desk.mp4 desk
```

完成后浏览器打开 **http://localhost:7007** 漫游。

> 从 Windows 侧调用时，命令内容必须用单引号包住，否则 `$HOME` 会被 PowerShell 提前展开：
> ```powershell
> wsl -d Ubuntu -- bash /mnt/d/job/3DGS/pipeline.sh /mnt/d/job/3DGS/data/desk.mp4 desk
> ```

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `app.py` | **网页控制台主程序**。Windows 侧运行，纯标准库，通过 `wsl.exe` 驱动 WSL 侧脚本 |
| `run_job.sh` | WSL 侧任务执行器：抽帧 → COLMAP → 训练 → 导出 PLY → 渲染预览 |
| `viewer.sh` | 官方 `ns-viewer` 的启停 |
| `pipeline.sh` | 纯命令行流水线（不依赖控制台） |
| `web/index.html` | 控制台页面 |
| `web/static/splat.js` | **自写 WebGL2 高斯泼溅播放器**，PLY 解析 + EWA 投影 + 计数排序 |
| `start_console.bat` | 双击启动入口 |
| `check_env.py` | GPU / PyTorch 环境验证 |

生成产物（均已被 `.gitignore` 排除）：

```
outputs*/<名称>/<方法>/<时间戳>/   训练产物 + config.yml + ckpt
web/assets/<名称>/splat.ply        网页播放器用的泼溅模型
renders*/<名称>/preview.mp4        沿训练轨迹渲染的预览视频
logs/                              各阶段日志 + 状态文件
```

---

## 拍摄指南（实测有效）

成功案例（`desk.mp4`，**188/188 全配准**）与失败案例（`1.mp4`，**2/32**）的差异：

| | ❌ 失败（博物馆） | ✅ 成功（桌子） |
|---|---|---|
| 相机运动 | 快速直线推进，**无闭环** | **环绕，有闭环** |
| 曝光 | 自动曝光剧烈跳变 | **锁定**（亮度波动 107~123） |
| 场景 | 移动行人污染特征 | **完全静止** |
| 相邻帧内点 | 大量归零（最低 **0**） | **139~234，零弱匹配** |

### 硬性要求

- **移动速度 0.3~0.5 m/s**（约正常步速的 1/3）
- **锁定曝光和对焦**：长按屏幕至出现「AE/AF 锁定」
- **关闭电子防抖（EIS/OIS）** —— 最反直觉但最重要。EIS 会裁剪画面并做非刚性形变，破坏针孔相机模型，直接导致内参估错
- **关闭 HDR / ProRAW / 美颜滤镜**
- 分辨率 1080p 或 4K，30 fps（60fps 快门时间短，室内容易糊）
- 时长 30~60 秒

### 手法

**环绕法（首选）**：以主体为中心，距离 1.5~2 米绕圈，每 15~20 度停 0.5 秒。拍 2~3 圈（不同高度）。**闭环**是关键——它能修正累积漂移。

**走廊推进法**：缓慢直线前进，到尽头后**转身 180 度原路走回**（回程提供闭环）。

### 场景选择

| ✅ 适合 | ❌ 避免 |
|---|---|
| 纹理丰富的物体（雕塑、书籍、植物） | 纯白墙、玻璃、镜面 |
| 哑光表面 | 抛光大理石、金属、水面 |
| 静态场景 | 走动的人、摇摆的树 |
| 均匀漫射光 | 强逆光、频闪光源 |

> **低纹理主体**（如纯白桌面、光滑皮革）：不致命但会让该区域特征点稀少。桌子案例中桌面 0 特征点，但靠沙发表面轮廓线和 188 帧高密度覆盖仍重建良好。若想让桌面更实，在上面放几件有纹理的物品（书、杯子、花纹布）即可。

---

## 六个关键坑（本项目的实际调试结论）

### 坑 1：抽帧太稀疏 → 位姿解算崩溃

**症状**：相邻帧匹配数断崖下跌到 0，COLMAP 只能配准 2/30 帧。

**原因**：走路速度约 1.4 m/s，2 FPS 抽帧时相邻帧位移过大，重叠不足。

**解法**：抽帧提到 **6 FPS**（相邻间隔 0.167 秒）。实测相邻帧内点数从 `0` 提升到 `369+`。

```
2 FPS:  f10:973  f11:610  f12:370  f13:159  f14:45  f25+:0   ← 崩溃
6 FPS:  最差 369，29 对全部健康                                 ← 正常
```

### 坑 2：OPENCV 相机模型让 Bundle Adjustment 发散

**症状**：
- mapper 日志刷 `Unable to perform dense Cholesky factorization`
- `Could not register, trying another image.` 反复出现
- 输出内参发散：`fl_x:2471  fl_y:6585  k1:1.04  k2:-9.44`

**原因**：nerfstudio 默认 `--camera-type perspective` → COLMAP 用 **OPENCV（8 参数）**。
但 COLMAP 初始全局 Bundle Adjustment 只有 **2 张图**，8 个内参从 2 视图欠定，BA 数值爆炸 →
后续所有图像 PnP 全部失败。

**解法**：加 `--camera-type pinhole` → COLMAP 用 **PINHOLE（4 参数）**。

映射表（`nerfstudio/process_data/process_data_utils.py`）：
```
perspective    -> OPENCV        (8 参数)  ❌
pinhole        -> PINHOLE       (4 参数)  ✅
simple_pinhole -> SIMPLE_PINHOLE (3 参数)
```

实测效果：

| | OPENCV | PINHOLE |
|---|---|---|
| 配准帧数 | 2 / 32 | **188 / 188** |
| 焦距一致性 | fx:1122 vs fy:1050（差 6.5%） | **fx:717.67 vs fy:720.08（差 0.3%）** |

### 坑 3：gsplat 的 CUDA 扩展根本没编译

**症状**：训练启动即崩
```
AttributeError: 'NoneType' object has no attribute 'CameraModelType'
```

**原因**：PyPI 上的 gsplat wheel 是 `py3-none-any.whl`（**纯 Python**），不含编译产物，
靠**首次导入时 JIT 编译**。若机器上没有 `nvcc`，它会静默打印
*"gsplat: No CUDA toolkit found. gsplat will be disabled."* 并设 `_C = None`，不报错退出。

**解法**（三步）：

```bash
# 1. 装 CUDA 工具链 + 老版本 gcc
#    系统默认 gcc 15 对 CUDA 12.1 太新，必须用 gcc-12
sudo apt-get install -y gcc-12 g++-12 nvidia-cuda-toolkit   # nvcc 12.4

# 2. 建 shim 让 nvcc/torch 找到 gcc-12（不动系统默认 gcc）
SHIM=$HOME/cuda12shim; mkdir -p $SHIM
ln -sf /usr/bin/gcc-12 $SHIM/gcc
ln -sf /usr/bin/g++-12 $SHIM/g++

# 3. 设环境并触发编译
export PATH=$SHIM:$HOME/envs/nerfstudio/bin:/usr/bin:$PATH
export CUDA_HOME=/usr
export MAX_JOBS=2        # ← 关键，见下
python -c "from gsplat.cuda._backend import _C; print(_C)"
```

> **`MAX_JOBS=2` 是硬约束**：本机内存仅 7.4GB。设 8 时所有 nvcc 全部
> `FAILED: [code=137]`（= SIGKILL = OOM）。编译产物缓存在
> `~/.cache/torch_extensions/py310_cu121/gsplat_cuda/`，后续无需重编。

### 坑 4：官方 Viewer 打不开（WSL 后台进程被回收）

**症状**：浏览器访问 `localhost:7007` 报 `ERR_CONNECTION_REFUSED`，且 `logs/viewer.log` 是**空文件**。

**原因**：`viewer.sh` 原本用 `setsid nohup ns-viewer ... &` 启动后脚本立即退出。
`wsl.exe` 一结束，WSL 就认为该命令没有前台工作了，把整个会话收走，后台的 ns-viewer 被连带杀掉。
**空日志正是铁证**——进程死得太早，连 viser 的 banner 都没来得及写入。

**解法**：让 **`wsl.exe` 的存活期 == viewer 的存活期**。

```bash
# viewer.sh：改为前台 exec（而不是后台 + 立即退出）
exec $PY/ns-viewer --load-config "$CFG" --viewer.websocket-port "$PORT"
```

```python
# app.py：改用 Popen 且不等待（用 subprocess.run 会阻塞到超时）
_VIEWER_PROC = subprocess.Popen([...], stdout=logf, stderr=subprocess.STDOUT)
for _ in range(60):                       # 最多等 30 秒
    if viewer_running():
        return True, "http://localhost:7007"
    time.sleep(0.5)
```

> 验证方法：启动后 `Get-CimInstance Win32_Process -Filter "Name='wsl.exe'"` 应能看到
> 一条 `ParentProcessId` 等于 `app.py` 的进程，且 90 秒后仍在。

**注意**：`run_job.sh` **没有**这个问题——它没有后台化，且 `job_thread` 用
`while proc.poll() is None:` 全程等待，wsl.exe 始终存活。

### 坑 5：WSL 路径与 Windows 路径混用

**症状**：控制台报「找不到 config.yml」，但文件明明存在。

**原因**：任务状态里存的 `config` 是 **WSL 路径**（`/mnt/d/job/3DGS/...`，由
`import_history` 用 `wsl_path()` 转换而来），直接用 Windows 的 `os.path.exists()`
去检查必然返回 `False`。

**解法**：需要在 Windows 侧判断存在性时，先用 `win_path()` 反向转换。

```python
def win_path(wsl_p):
    """/mnt/d/a/b -> D:\a\b；非 /mnt/ 开头的原样返回"""
    if wsl_p and wsl_p.startswith("/mnt/") and len(wsl_p) > 6:
        return wsl_p[5].upper() + ":" + wsl_p[6:].replace("/", "\\")
    return wsl_p
```

**反向坑**：把已经是 WSL 路径的 `config` 再包一层 `wsl_path()` 会变成
`/mnt/d/mnt/d/...`。传给 WSL 侧命令时**直接传原值**即可（与 `_export_thread` 的约定一致）。

### 坑 6：播放器里画面躺倒

**症状**：模型加载成功但整体翻转 90°。

**原因**：`ns-export gaussian-splat --ply-color-mode rgb` 导出的 PLY 头写着
`Vertical Axis: z`，而多数播放器按 Y 轴朝上解释。

**解法**：勾选网页播放器里的「**按 Z 轴朝上**」复选框即可，不需要重新导出。

> 同时注意该格式的数值约定（播放器 `splat.js` 内已做反变换）：
> - `opacity` 存的是 **logit** → 需过 `sigmoid`
> - `scale` 存的是 **log** → 需过 `exp`
> - 四元数未归一化 → 需 normalize；`rot_0` 即 **w**（wxyz 约定）

---

## 环境信息

| 组件 | 版本 / 路径 |
|---|---|
| Windows 侧 | Python 3.12.8 @ `D:\python\3.12.8\python.exe`（控制台，纯标准库） |
| 系统 | WSL2 Ubuntu（内存 7.4GB，24 核） |
| GPU | RTX 4060 Laptop 8GB（直通） |
| Python | 3.10.22 @ `/home/fjy001/envs/nerfstudio` |
| PyTorch | 2.5.1+cu121 |
| nerfstudio | 1.1.5 |
| gsplat | 1.4.0（CUDA 扩展已编译，缓存于 `~/.cache/torch_extensions/`） |
| COLMAP | 3.12.6（apt 安装） |
| CUDA toolkit | 12.4.131（apt `nvidia-cuda-toolkit`） |
| gcc | 12.5.0（via shim） |

**调用约定**：
- conda: `$HOME/miniconda3/bin/conda`
- 程序: `$HOME/envs/nerfstudio/bin/<cmd>`
- apt 需 root: `wsl -d Ubuntu -u root -- bash -c "..."`（sudo 需密码无法交互）
- 长任务: `setsid nohup <script> > log 2>&1 < /dev/null &` 后轮询，避开 shell 工具 600 秒超时
- **PowerShell 调 WSL 的引号陷阱**：`bash -c` 的内容要用**单引号**包住，
  否则 `$HOME` 被 PowerShell 展开、反斜杠路径被吞掉；路径一律写 `/mnt/d/...` 格式

---

## 排错速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 浏览器 `ERR_CONNECTION_REFUSED` @7007 | viewer 后台启动被 WSL 回收 | 见坑 4，确认 `viewer.log` 非空 |
| 报「找不到 config.yml」但文件存在 | WSL 路径直接喂给 Windows API | 见坑 5，用 `win_path()` |
| `AttributeError: 'NoneType' ... CameraModelType` | gsplat 未编译 | 见坑 3 |
| `FAILED: [code=137]` | 编译 OOM | `MAX_JOBS=2` |
| 配准率 <90% | 移速过快 / 移动物体 / 曝光跳变 | 见拍摄指南 |
| `Could not register` 刷屏 | 内参发散 | 确认用了 `--camera-type pinhole` |
| `$'\r': command not found` | `.sh` 被转成 CRLF | 检查 `.gitattributes`，`git add --renormalize .` |
| 画面整体躺倒 | PLY 垂直轴是 z | 勾选播放器「按 Z 轴朝上」 |
| `vocab_tree.fbow` 格式报错 | COLMAP 3.12 改用 faiss | 改用 `sequential` 匹配 |
| `libPoseLib.so` 缺失 | apt colmap 缺依赖 | `apt-get install libposelib` |
| `FileNotFoundError: images_2/...` | 重跑时残留目录冲突 | 删除输出目录后重跑 |
| 训练 `out of memory` | 显存不足 | 降低分辨率或加 `--pipeline.model.num-downscales 1` |

---

## 关于本仓库

仓库**只包含源码、脚本与文档**，所有训练产物、视频、日志均已由 `.gitignore` 排除（约 1.5GB）。

### 首次克隆后需要做的事

```bash
# 1. WSL 侧装好环境（见「坑 3」）
# 2. Windows 侧无需安装任何 pip 包，直接双击 start_console.bat
# 3. 上传视频开始训练
```

### 行尾约定

`.gitattributes` 已锁定，**不要**关掉它：

- `*.sh` → **LF**。若被转成 CRLF，WSL 里的 bash 会全部报 `$'\r': command not found`
- `*.bat` → **CRLF**。cmd.exe 对纯 LF 的多行 `if (...)` 块解析不稳定
- 其余文本统一 LF

若已克隆到一个行尾错乱的仓库，执行：

```bash
git add --renormalize .
git commit -m "fix line endings"
```

### 上传到 Gitee

```bash
# 在项目目录下初始化
git init
git add .
git commit -m "3DGS 视频重建流水线：网页控制台 + WebGL2 播放器"

# 关联远程仓库（替换成你自己的地址）
git remote add origin https://gitee.com/<你的用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```

> **中文文件名提示**：项目里已无中文文件名（启动脚本为 `start_console.bat`）。
> 若后续新增中文文件名，建议先执行 `git config core.quotepath false`，
> 否则 `git status` 会把它们显示成 `\346\216\247...` 这样的转义序列。

---

## 参考资料

- [3D Gaussian Splatting for Real-Time Radiance Field Rendering](https://repo-sam.inria.fr/fungraph/3d-gaussian-splatting/)
- [nerfstudio](https://github.com/nerfstudio-project/nerfstudio)
- [gsplat](https://github.com/nerfstudio-project/gsplat)
