# -*- coding: utf-8 -*-
"""
GF-7 DLC 批量预处理（4 景，不裁剪）
每景流程：BWDMUX 定标 → 补波长 → QUAC → RPC 正射(3.2m, Nearest Neighbor)
        → BWDPAN 正射(0.8m, 原始 DN, Bilinear) → NNDiffuse 融合
保留：pansharp.dat（0.8m 融合）+ mux_ortho.dat（3.2m 光谱，供群落分类）
全部完成后自动关机（60 秒缓冲）。
"""
import os
import sys
import json
import time
import glob
import subprocess
import argparse
import xml.etree.ElementTree as ET

from envipyengine import Engine

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

parser = argparse.ArgumentParser(description="GF-7 DLC 批量预处理")
parser.add_argument("scenes", nargs="*", help="指定场景名（默认处理全部）")
parser.add_argument("--shutdown", action="store_true", help="全部完成后自动关机")
args = parser.parse_args()

# ====== 配置（按你的环境修改） ======
BASE = r"."                     # 场景目录所在位置
OUT_BASE = r"./processed"         # 输出目录
DEM = r"C:\Program Files\ENVI\data\GMTED2010.jp2"  # ENVI 自带 DEM 路径
WAVELENGTH = [485.0, 555.0, 660.0, 830.0]   # GF-7 DLC 多光谱中心波长 nm

os.makedirs(OUT_BASE, exist_ok=True)
LOG = os.path.join(OUT_BASE, "batch_log.txt")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_gain(tiff_path):
    """从同目录同名 .xml 读 AGain（兼容 AbsCeof）。"""
    xml_path = os.path.splitext(tiff_path)[0] + ".xml"
    root = ET.parse(xml_path).getroot()
    text = root.findtext("AGain") or root.findtext("AbsCeof") or ""
    gain = [float(x) for x in text.split(",") if x.strip()]
    if not gain:
        raise RuntimeError(f"{xml_path} 无 AGain/AbsCeof")
    return gain


def utm_zone(tiff_path):
    """UTM 带号：xml ZoneNo 优先，否则按中心经度。"""
    xml_path = os.path.splitext(tiff_path)[0] + ".xml"
    root = ET.parse(xml_path).getroot()
    zone = root.findtext("ZoneNo")
    if zone and zone.strip():
        return int(float(zone))
    lon = float(root.findtext("CenterLongitude"))
    return int((lon + 180) / 6) + 1


def rst(path):
    return {"factory": "URLRaster", "url": path}


def coord_sys(zone):
    return {"factory": "CoordSys", "coord_sys_code": 32600 + zone}


def check(path, tag):
    ok = os.path.exists(path) and os.path.getsize(path) > 0
    size = os.path.getsize(path) if os.path.exists(path) else 0
    log(f"[{tag}] {'OK' if ok else 'FAIL'} {path} ({size/1e9:.2f} GB)")
    if not ok:
        raise RuntimeError(f"{tag} 输出缺失: {path}")


def cleanup(*paths):
    for p in paths:
        if p and os.path.exists(p):
            os.remove(p)
            log(f"[清理] 删除 {p}")


def ensure_wavelength(hdr_path):
    with open(hdr_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    if "wavelength" in content.lower():
        return
    block = ("\nwavelength = {\n " + ", ".join(f"{w:.6f}" for w in WAVELENGTH) +
             "}\nwavelength units = Nanometers\n")
    with open(hdr_path, "a", encoding="utf-8") as f:
        f.write(block)
    log(f"[波长] 已写入 {hdr_path}")


scenes = sorted(glob.glob(os.path.join(BASE, "GF7_DLC_*")))
scenes = [s for s in scenes if os.path.isdir(s)]
if args.scenes:
    scenes = [s for s in scenes if os.path.basename(s) in args.scenes]
log(f"发现 {len(scenes)} 个场景: {[os.path.basename(s) for s in scenes]}")
if not scenes:
    log("未找到场景目录，终止")
    sys.exit(1)

engine = Engine("ENVI")
results = []

for scene in scenes:
    name = os.path.basename(scene)
    log(f"########## 开始处理: {name} ##########")
    try:
        mux = glob.glob(os.path.join(scene, "*BWDMUX.tiff"))[0]
        pan = glob.glob(os.path.join(scene, "*BWDPAN.tiff"))[0]
        out = os.path.join(OUT_BASE, name)
        os.makedirs(out, exist_ok=True)
        zone = utm_zone(mux)
        log(f"UTM 带号: {zone}N (EPSG:{32600 + zone})")

        # 定标系数检测：有 AGain → 完整模式（定标+QUAC）；无 → 几何模式（仅正射+融合）
        try:
            gain = read_gain(mux)
            mode = "full"
            log(f"定标系数: {gain} → 完整模式（定标+大气校正）")
        except Exception as e:
            gain = None
            mode = "geo"
            log(f"无定标系数（{str(e)[:80]}）→ 几何模式（仅正射+融合，无反射率产品）")

        mux_source = mux
        if mode == "full":
            # 步骤 1: MUX 定标
            log("-- 步骤 1/5: 多光谱辐射定标 --")
            mux_cal = os.path.join(out, "mux_cal.dat")
            res = engine.task("ApplyGainOffset").execute({
                "INPUT_RASTER": rst(mux), "GAIN": gain,
                "OFFSET": [0.0] * len(gain), "OUTPUT_RASTER_URI": mux_cal})
            check(mux_cal, "MUX定标")
            ensure_wavelength(mux_cal.replace(".dat", ".hdr"))

            # 步骤 2: QUAC
            log("-- 步骤 2/5: QUAC 大气校正 --")
            mux_quac = os.path.join(out, "mux_quac.dat")
            res = engine.task("QUAC").execute({
                "INPUT_RASTER": rst(mux_cal), "OUTPUT_RASTER_URI": mux_quac})
            check(mux_quac, "QUAC")
            cleanup(mux_cal, mux_cal.replace(".dat", ".hdr"))
            mux_source = mux_quac
        else:
            log("-- 跳过辐射定标与大气校正（几何模式）--")

        # 步骤 3: MUX 正射（3.2m, Nearest Neighbor 保持光谱纯正, 供群落分类）
        log(f"-- 步骤 3/{5 if mode == 'full' else 3}: 多光谱 RPC 正射 (3.2m, Nearest Neighbor) --")
        mux_ortho = os.path.join(out, "mux_ortho.dat")
        res = engine.task("RPCOrthorectification").execute({
            "INPUT_RASTER": rst(mux_source), "DEM_RASTER": rst(DEM),
            "OUTPUT_COORDINATE_SYSTEM": coord_sys(zone),
            "OUTPUT_PIXEL_SIZE": [3.2, 3.2],
            "RESAMPLING": "Nearest Neighbor",
            "OUTPUT_RASTER_URI": mux_ortho})
        check(mux_ortho, "MUX正射")
        if mode == "full":
            cleanup(mux_quac, mux_quac.replace(".dat", ".hdr"))

        # 步骤 4: PAN 正射（0.8m, 原始 DN, Bilinear）
        log(f"-- 步骤 4/{5 if mode == 'full' else 3}: 全色 RPC 正射 (0.8m, 原始 DN, Bilinear) --")
        pan_ortho = os.path.join(out, "pan_ortho.dat")
        res = engine.task("RPCOrthorectification").execute({
            "INPUT_RASTER": rst(pan), "DEM_RASTER": rst(DEM),
            "OUTPUT_COORDINATE_SYSTEM": coord_sys(zone),
            "OUTPUT_PIXEL_SIZE": [0.8, 0.8],
            "RESAMPLING": "Bilinear",
            "OUTPUT_RASTER_URI": pan_ortho})
        check(pan_ortho, "PAN正射")

        # 步骤 5: 融合
        log(f"-- 步骤 5/{5 if mode == 'full' else 3}: NNDiffuse 融合 --")
        pansharp = os.path.join(out, "pansharp.dat")
        res = engine.task("NNDiffusePanSharpening").execute({
            "INPUT_LOW_RESOLUTION_RASTER": rst(mux_ortho),
            "INPUT_HIGH_RESOLUTION_RASTER": rst(pan_ortho),
            "OUTPUT_RASTER_URI": pansharp})
        check(pansharp, "融合")
        cleanup(pan_ortho, pan_ortho.replace(".dat", ".hdr"))

        log(f"########## {name} 完成：{pansharp} ##########")
        results.append((name, "OK"))
    except Exception as e:
        log(f"########## {name} 失败: {str(e)[:300]} ##########")
        results.append((name, f"FAIL: {str(e)[:200]}"))

log("========== 全部场景处理结束 ==========")
for r in results:
    log(f"结果: {r[0]} -> {r[1]}")
if args.shutdown:
    log("任务完成，60 秒后自动关机（如需取消请运行: shutdown /a）")
    try:
        subprocess.call(["shutdown", "/s", "/t", "60", "/c", "GF7 batch done"])
    except Exception as e:
        log(f"关机命令执行失败: {e}")
else:
    log("未指定 --shutdown，不关机")
