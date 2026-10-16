// ============================================================
// 轻量 3D Gaussian Splatting 播放器（WebGL2，无外部依赖）
// 输入: ns-export gaussian-splat --ply-color-mode rgb 产出的 .ply
//       （opacity 为 logit，scale 为 log，quat 未归一化）
// 操作: 左键拖动=旋转  滚轮=缩放  右键拖动=平移
// ============================================================
"use strict";

const MAX_TEX_WIDTH = 2048;

// ---------- PLY 解析 ----------
function parsePLY(buffer) {
    const bytes = new Uint8Array(buffer);
    // 找 header 结尾
    let headerEnd = -1;
    const lim = Math.min(bytes.length, 65536);
    const decoder = new TextDecoder("ascii");
    const headText = decoder.decode(bytes.subarray(0, lim));
    const marker = "end_header";
    const mi = headText.indexOf(marker);
    if (mi < 0) throw new Error("不是合法的 PLY 文件");
    headerEnd = headText.indexOf("\n", mi) + 1;

    const lines = headText.slice(0, mi).split(/\r?\n/);
    let count = 0, format = "";
    const props = [];
    for (const ln of lines) {
        const t = ln.trim().split(/\s+/);
        if (t[0] === "format") format = t[1];
        else if (t[0] === "element" && t[1] === "vertex") count = parseInt(t[2], 10);
        else if (t[0] === "property" && t[1] !== "list") props.push({ type: t[1], name: t[2] });
    }
    if (format !== "binary_little_endian") throw new Error("仅支持 binary_little_endian PLY");
    const SIZE = { float: 4, float32: 4, double: 8, uchar: 1, uint8: 1, char: 1, int: 4, short: 2, ushort: 2 };
    let stride = 0;
    for (const p of props) { p.off = stride; stride += SIZE[p.type] || 4; }

    const view = new DataView(buffer);
    const base = headerEnd;
    const idx = {};
    props.forEach((p, i) => idx[p.name] = i);

    const need = ["x", "y", "z", "opacity", "scale_0", "scale_1", "scale_2",
                  "rot_0", "rot_1", "rot_2", "rot_3"];
    for (const k of need) if (!(k in idx)) throw new Error("PLY 缺少字段: " + k);

    const getF = (i, name) => {
        const p = props[idx[name]];
        const a = base + i * stride + p.off;
        return p.type === "double" ? view.getFloat64(a, true) : view.getFloat32(a, true);
    };

    const hasRGB = ("red" in idx) && ("green" in idx) && ("blue" in idx);
    const hasFDC = ("f_dc_0" in idx);

    // ---- 组装渲染数据 ----
    // 每个 splat 占 4 个 RGBA32F texel:
    //   t0: center.xyz + opacity(sigmoid后)
    //   t1: cov3d xx, xy, xz, yy
    //   t2: cov3d yz, zz, 0, 0
    //   t3: color.rgb, 0
    const texW = MAX_TEX_WIDTH;
    const texH = Math.ceil(count * 4 / texW);
    const data = new Float32Array(texW * texH * 4);
    const centers = new Float32Array(count * 3);   // 供包围盒/排序用

    const sigmoid = x => 1 / (1 + Math.exp(-x));
    // SH DC 基函数 C0
    const SH_C0 = 0.28209479177387814;

    for (let i = 0; i < count; i++) {
        const x = getF(i, "x"), y = getF(i, "y"), z = getF(i, "z");
        centers[i * 3] = x; centers[i * 3 + 1] = y; centers[i * 3 + 2] = z;

        const op = sigmoid(getF(i, "opacity"));
        const s0 = Math.exp(getF(i, "scale_0"));
        const s1 = Math.exp(getF(i, "scale_1"));
        const s2 = Math.exp(getF(i, "scale_2"));

        let qw = getF(i, "rot_0"), qx = getF(i, "rot_1"), qy = getF(i, "rot_2"), qz = getF(i, "rot_3");
        const qlen = Math.hypot(qw, qx, qy, qz) || 1;
        qw /= qlen; qx /= qlen; qy /= qlen; qz /= qlen;

        // 旋转矩阵 R (列向量)
        const r00 = 1 - 2 * (qy * qy + qz * qz), r01 = 2 * (qx * qy - qw * qz), r02 = 2 * (qx * qz + qw * qy);
        const r10 = 2 * (qx * qy + qw * qz), r11 = 1 - 2 * (qx * qx + qz * qz), r12 = 2 * (qy * qz - qw * qx);
        const r20 = 2 * (qx * qz - qw * qy), r21 = 2 * (qy * qz + qw * qx), r22 = 1 - 2 * (qx * qx + qy * qy);

        // M = R * S,  cov = M * M^T
        const m00 = r00 * s0, m01 = r01 * s1, m02 = r02 * s2;
        const m10 = r10 * s0, m11 = r11 * s1, m12 = r12 * s2;
        const m20 = r20 * s0, m21 = r21 * s1, m22 = r22 * s2;
        const c00 = m00 * m00 + m01 * m01 + m02 * m02;
        const c01 = m00 * m10 + m01 * m11 + m02 * m12;
        const c02 = m00 * m20 + m01 * m21 + m02 * m22;
        const c11 = m10 * m10 + m11 * m11 + m12 * m12;
        const c12 = m10 * m20 + m11 * m21 + m12 * m22;
        const c22 = m20 * m20 + m21 * m21 + m22 * m22;

        let r, g, b;
        if (hasRGB) {
            const gs = p => props[idx[p]].type === "uchar" ? getU8(i, p) / 255 : getF(i, p);
            r = gs("red"); g = gs("green"); b = gs("blue");
        } else if (hasFDC) {
            r = 0.5 + SH_C0 * getF(i, "f_dc_0");
            g = 0.5 + SH_C0 * getF(i, "f_dc_1");
            b = 0.5 + SH_C0 * getF(i, "f_dc_2");
        } else { r = g = b = 0.7; }

        const o = i * 4 * 4;
        data[o] = x; data[o + 1] = y; data[o + 2] = z; data[o + 3] = op;
        data[o + 4] = c00; data[o + 5] = c01; data[o + 6] = c02; data[o + 7] = c11;
        data[o + 8] = c12; data[o + 9] = c22; data[o + 10] = 0; data[o + 11] = 0;
        data[o + 12] = r; data[o + 13] = g; data[o + 14] = b; data[o + 15] = 0;
    }

    function getU8(i, name) {
        const p = props[idx[name]];
        return bytes[base + i * stride + p.off];
    }

    return { data, centers, count, texW, texH };
}

// ---------- 4x4 矩阵工具（列主序，与 WebGL 一致） ----------
function mat4Perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0,
                             0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
}
function mat4LookAt(eye, target, up) {
    const zx = eye[0] - target[0], zy = eye[1] - target[1], zz = eye[2] - target[2];
    let zl = Math.hypot(zx, zy, zz) || 1;
    const z = [zx / zl, zy / zl, zz / zl];
    const x = [up[1] * z[2] - up[2] * z[1], up[2] * z[0] - up[0] * z[2], up[0] * z[1] - up[1] * z[0]];
    let xl = Math.hypot(x[0], x[1], x[2]) || 1;
    x[0] /= xl; x[1] /= xl; x[2] /= xl;
    const y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]];
    return new Float32Array([
        x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
        -(x[0] * eye[0] + x[1] * eye[1] + x[2] * eye[2]),
        -(y[0] * eye[0] + y[1] * eye[1] + y[2] * eye[2]),
        -(z[0] * eye[0] + z[1] * eye[1] + z[2] * eye[2]), 1,
    ]);
}

// ---------- 着色器 ----------
const VERT = `#version 300 es
precision highp float; precision highp int;
in vec2 aQuad;
in uint aIndex;
uniform sampler2D uData;
uniform mat4 uView;
uniform mat4 uProj;
uniform vec2 uFocal;
uniform vec2 uViewport;
uniform int uTexW;
out vec2 vXY;
out vec3 vConic;
out vec4 vColor;

void main() {
    int idx = int(aIndex);
    int t0i = idx * 4;
    ivec2 uv = ivec2(t0i % uTexW, t0i / uTexW);
    vec4 T0 = texelFetch(uData, uv, 0);
    vec4 T1 = texelFetch(uData, uv + ivec2(1, 0), 0);
    vec4 T2 = texelFetch(uData, uv + ivec2(2, 0), 0);
    vec4 T3 = texelFetch(uData, uv + ivec2(3, 0), 0);

    vec3 center = T0.xyz;
    float opacity = T0.w;
    mat3 Vrk = mat3(T1.x, T1.y, T1.z,
                    T1.y, T2.x, T2.y,
                    T1.z, T2.y, T2.z);

    vec4 camPos = uView * vec4(center, 1.0);
    if (camPos.z > -0.05) { gl_Position = vec4(0.0, 0.0, 2.0, 1.0); vXY = vec2(0.0); vConic = vec3(0.0); vColor = vec4(0.0); return; }

    // 2D 协方差投影（EWA splatting）
    float fx = uFocal.x, fy = uFocal.y;
    float invZ = 1.0 / camPos.z;
    vec3 row0 = vec3(fx * invZ, 0.0, -fx * camPos.x * invZ * invZ);
    vec3 row1 = vec3(0.0, fy * invZ, -fy * camPos.y * invZ * invZ);
    mat3 W = mat3(uView);
    vec3 Tr0 = row0 * W;
    vec3 Tr1 = row1 * W;
    vec3 A = Tr0 * Vrk;
    vec3 B = Tr1 * Vrk;
    float a = dot(A, Tr0) + 0.3;
    float b = dot(A, Tr1);
    float c = dot(B, Tr1) + 0.3;
    float det = a * c - b * b;
    if (det <= 0.0) { gl_Position = vec4(0.0, 0.0, 2.0, 1.0); vXY = vec2(0.0); vConic = vec3(0.0); vColor = vec4(0.0); return; }
    vec3 conic = vec3(c, -b, a) / det;

    float mid = 0.5 * (a + c);
    float l1 = mid + sqrt(max(0.1, mid * mid - det));
    float l2 = mid - sqrt(max(0.1, mid * mid - det));
    float radius = ceil(3.0 * sqrt(max(l1, l2)));

    vec4 clipPos = uProj * camPos;
    vec2 offsetPx = aQuad * radius;
    vec2 offsetNdc = offsetPx / uViewport * 2.0;
    gl_Position = vec4(clipPos.xy + offsetNdc * clipPos.w, clipPos.z, clipPos.w);

    vXY = offsetPx;
    vConic = conic;
    vColor = vec4(T3.rgb, opacity);
}`;

const FRAG = `#version 300 es
precision highp float;
in vec2 vXY;
in vec3 vConic;
in vec4 vColor;
out vec4 fragColor;
void main() {
    float power = -0.5 * (vConic.x * vXY.x * vXY.x + vConic.z * vXY.y * vXY.y) - vConic.y * vXY.x * vXY.y;
    if (power > 0.0) discard;
    float alpha = min(0.99, vColor.a * exp(power));
    if (alpha < 0.00392156863) discard;
    fragColor = vec4(vColor.rgb * alpha, alpha);   // 预乘 alpha
}`;

function compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
        throw new Error("着色器编译失败: " + gl.getShaderInfoLog(s));
    return s;
}

// ---------- 播放器 ----------
class SplatViewer {
    constructor(canvas) {
        this.canvas = canvas;
        const gl = canvas.getContext("webgl2", { antialias: false, alpha: false });
        if (!gl) throw new Error("浏览器不支持 WebGL2");
        this.gl = gl;

        const prog = gl.createProgram();
        gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
        gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
            throw new Error("着色器链接失败: " + gl.getProgramInfoLog(prog));
        this.prog = prog;
        gl.useProgram(prog);
        this.u = {};
        for (const n of ["uData", "uView", "uProj", "uFocal", "uViewport", "uTexW"])
            this.u[n] = gl.getUniformLocation(prog, n);

        // 四边形（两个三角形）
        this.quadBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, this.quadBuf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
            -1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]), gl.STATIC_DRAW);
        const aQuad = gl.getAttribLocation(prog, "aQuad");
        gl.enableVertexAttribArray(aQuad);
        gl.vertexAttribPointer(aQuad, 2, gl.FLOAT, false, 0, 0);

        // 排序后的索引（实例属性）
        this.indexBuf = gl.createBuffer();
        this.aIndex = gl.getAttribLocation(prog, "aIndex");
        gl.bindBuffer(gl.ARRAY_BUFFER, this.indexBuf);
        gl.enableVertexAttribArray(this.aIndex);
        gl.vertexAttribIPointer(this.aIndex, 1, gl.UNSIGNED_INT, 0, 0);
        gl.vertexAttribDivisor(this.aIndex, 1);

        this.tex = gl.createTexture();
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, this.tex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.uniform1i(this.u.uData, 0);

        gl.disable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND);
        gl.blendFuncSeparate(gl.ONE, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        gl.clearColor(0.07, 0.08, 0.10, 1.0);

        // 相机状态
        this.yaw = 0; this.pitch = 0; this.dist = 10;
        this.target = [0, 0, 0]; this.up = [0, 1, 0];
        this.zUp = false;
        this.count = 0;
        this.fps = 0; this._frames = 0; this._lastT = performance.now();
        this._needSort = true;

        this._bindInput();
    }

    setScene(scene) {
        const gl = this.gl;
        this.scene = scene;
        this.count = scene.count;
        this.order = new Uint32Array(scene.count);
        this.depth = new Float32Array(scene.count);

        gl.bindTexture(gl.TEXTURE_2D, this.tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, scene.texW, scene.texH, 0,
                      gl.RGBA, gl.FLOAT, scene.data);
        gl.bindBuffer(gl.ARRAY_BUFFER, this.indexBuf);
        gl.bufferData(gl.ARRAY_BUFFER, this.order.byteLength, gl.DYNAMIC_DRAW);

        // 包围盒 -> 初始相机
        const c = scene.centers;
        let mn = [1e30, 1e30, 1e30], mx = [-1e30, -1e30, -1e30];
        const step = Math.max(1, Math.floor(scene.count / 50000));
        for (let i = 0; i < scene.count; i += step) {
            for (let k = 0; k < 3; k++) {
                const v = c[i * 3 + k];
                if (v < mn[k]) mn[k] = v;
                if (v > mx[k]) mx[k] = v;
            }
        }
        this.target = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
        const radius = Math.max(0.1, Math.hypot(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]) / 2);
        this.dist = radius * 2.2;
        this.yaw = Math.PI * 0.25; this.pitch = -0.25;
        this._needSort = true;
    }

    setZUp(on) {
        this.zUp = on;
        this.up = on ? [0, 0, 1] : [0, 1, 0];
        this._needSort = true;
    }

    _bindInput() {
        const cv = this.canvas;
        let dragging = 0, lx = 0, ly = 0;
        cv.addEventListener("contextmenu", e => e.preventDefault());
        cv.addEventListener("pointerdown", e => {
            dragging = e.button === 2 ? 2 : 1;
            lx = e.clientX; ly = e.clientY;
            cv.setPointerCapture(e.pointerId);
        });
        cv.addEventListener("pointerup", () => dragging = 0);
        cv.addEventListener("pointermove", e => {
            if (!dragging) return;
            const dx = e.clientX - lx, dy = e.clientY - ly;
            lx = e.clientX; ly = e.clientY;
            if (dragging === 1) {
                this.yaw -= dx * 0.008;
                this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch - dy * 0.008));
            } else {
                const s = this.dist * 0.0016;
                const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
                this.target[0] += (-dx * cy - dy * Math.sin(this.pitch) * sy) * s;
                this.target[1] += (-dx * 0 + dy * Math.cos(this.pitch)) * s * (this.zUp ? 0 : 1);
                this.target[2] += (dx * sy - dy * Math.sin(this.pitch) * cy) * s;
                if (this.zUp) { this.target[1] += dy * Math.cos(this.pitch) * s; }
            }
            this._needSort = true;
        });
        cv.addEventListener("wheel", e => {
            e.preventDefault();
            this.dist *= Math.exp(e.deltaY * 0.0012);
            this.dist = Math.max(0.05, this.dist);
            this._needSort = true;
        }, { passive: false });
    }

    _sort() {
        const n = this.count;
        const v = this._view;
        const d = this.depth, order = this.order, c = this.scene.centers;
        // 相机空间 z（列主序 view 矩阵第 3 行）
        const r2x = v[2], r2y = v[6], r2z = v[10], r2w = v[14];
        let mn = Infinity, mx = -Infinity;
        for (let i = 0; i < n; i++) {
            const z = r2x * c[i * 3] + r2y * c[i * 3 + 1] + r2z * c[i * 3 + 2] + r2w;
            d[i] = z;
            if (z < mn) mn = z;
            if (z > mx) mx = z;
        }
        // 计数排序：z 从小（远）到大（近） -> 背景先画
        const NBUCKET = 65536;
        const range = mx - mn || 1;
        const counts = this._counts || (this._counts = new Uint32Array(NBUCKET + 1));
        counts.fill(0);
        const scale = (NBUCKET - 1) / range;
        const buckets = this._buckets || (this._buckets = new Uint32Array(n));
        for (let i = 0; i < n; i++) {
            const b = ((d[i] - mn) * scale) | 0;
            buckets[i] = b;
            counts[b + 1]++;
        }
        for (let b = 0; b < NBUCKET; b++) counts[b + 1] += counts[b];
        for (let i = 0; i < n; i++) order[counts[buckets[i]]++] = i;
    }

    render() {
        if (!this.scene) return;
        const gl = this.gl, cv = this.canvas;
        const w = cv.clientWidth, h = cv.clientHeight;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const W = Math.round(w * dpr), H = Math.round(h * dpr);
        if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
        gl.viewport(0, 0, W, H);

        const cy = Math.cos(this.pitch), sy = Math.sin(this.pitch);
        const cx = Math.cos(this.yaw), sx = Math.sin(this.yaw);
        // 绕 target 的球面相机
        let dir = [cy * sx, sy, cy * cx];
        if (this.zUp) dir = [cy * sx, cy * cx, sy];
        const eye = [this.target[0] + dir[0] * this.dist,
                     this.target[1] + dir[1] * this.dist,
                     this.target[2] + dir[2] * this.dist];
        const view = mat4LookAt(eye, this.target, this.up);
        this._view = view;

        if (this._needSort) { this._sort(); this._needSort = false; }

        const fov = 50 * Math.PI / 180;
        const proj = mat4Perspective(fov, W / H, Math.max(0.01, this.dist * 0.01), this.dist * 20);
        const focalY = (H / 2) / Math.tan(fov / 2);
        const focalX = focalY;   // 像素正方形

        gl.useProgram(this.prog);
        gl.uniformMatrix4fv(this.u.uView, false, view);
        gl.uniformMatrix4fv(this.u.uProj, false, proj);
        gl.uniform2f(this.u.uFocal, focalX, focalY);
        gl.uniform2f(this.u.uViewport, W, H);
        gl.uniform1i(this.u.uTexW, this.scene.texW);

        gl.bindBuffer(gl.ARRAY_BUFFER, this.indexBuf);
        gl.bufferSubData(gl.ARRAY_BUFFER, 0, this.order);
        gl.bindTexture(gl.TEXTURE_2D, this.tex);

        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, this.count);

        this._frames++;
        const now = performance.now();
        if (now - this._lastT > 500) {
            this.fps = this._frames * 1000 / (now - this._lastT);
            this._frames = 0; this._lastT = now;
            if (this.onStats) this.onStats(this.fps, this.count);
        }
    }
}

window.SplatViewer = SplatViewer;
window.parsePLY = parsePLY;
