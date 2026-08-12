# -*- coding: utf-8 -*-
"""
GF-6 PMS 批量预处理（平铺目录版，5 景，不裁剪）
"""
import os, sys, json, time, glob, subprocess, argparse, xml.etree.ElementTree as ET
from envipyengine import Engine

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

parser = argparse.ArgumentParser()
parser.add_argument("--shutdown", action="store_true", help="完成后自动关机")
args = parser.parse_args()

# ====== 配置（按你的环境修改） ======
BASE = r"."                    # 影像所在目录（平铺模式）
OUT_BASE = r"./processed"        # 输出目录
DEM = r"C:\Program Files\ENVI\data\GMTED2010.jp2"  # ENVI 自带 DEM 路径
WAVELENGTH = [485.0, 555.0, 660.0, 830.0]

os.makedirs(OUT_BASE, exist_ok=True)
LOG = os.path.join(OUT_BASE, "batch_log.txt")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_gain(tiff_path):
    xml_path = os.path.splitext(tiff_path)[0] + ".xml"
    root = ET.parse(xml_path).getroot()
    text = root.findtext("AGain") or root.findtext("AbsCeof") or ""
    gain = [float(x) for x in text.split(",")
            if x.strip() and x.strip().upper() != "NULL"]
    if not gain:
        raise RuntimeError("无有效定标系数")
    return gain


def utm_zone(tiff_path):
    xml_path = os.path.splitext(tiff_path)[0] + ".xml"
    root = ET.parse(xml_path).getroot()
    zone = root.findtext("ZoneNo")
    if zone and zone.strip():
        return int(float(zone))
    lon = float(root.findtext("CenterLongitude"))
    return int((lon + 180) / 6) + 1


def rst(p): return {"factory": "URLRaster", "url": p}


def coord_sys(z): return {"factory": "CoordSys", "coord_sys_code": 32600 + z}


def check(path, tag):
    ok = os.path.exists(path) and os.path.getsize(path) > 0
    size = os.path.getsize(path) if ok else 0
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


mux_tiffs = sorted(glob.glob(os.path.join(BASE, "*MUX.tiff")) +
                   glob.glob(os.path.join(BASE, "*MSS.tiff")))
scenes = [(t.replace("-MUX.tiff", "").replace("-MSS.tiff", ""), t) for t in mux_tiffs]
log(f"发现 {len(scenes)} 景 PMS")

engine = Engine("ENVI")
results = []

for prefix, mux in scenes:
    name = os.path.basename(prefix)
    pan_cands = glob.glob(os.path.join(BASE, os.path.basename(prefix) + "-PAN*.tiff"))
    pan = pan_cands[0] if pan_cands else None
    if not pan:
        log(f"跳过 {name}：无 PAN 全色文件")
        continue

    out = os.path.join(OUT_BASE, name)
    os.makedirs(out, exist_ok=True)
    log(f"########## 开始: {name} ##########")
    try:
        zone = utm_zone(mux)
        log(f"UTM zone: {zone}N")

        # 检测模式
        try:
            gain = read_gain(mux)
            mode = "full"
            log(f"定标系数: {gain} → 完整模式")
        except Exception as e:
            gain = None
            mode = "geo"
            log(f"无有效系数（{str(e)[:80]}）→ 几何模式")

        mux_source = mux
        if mode == "full":
            log("-- 定标 --")
            mux_cal = os.path.join(out, "mux_cal.dat")
            engine.task("ApplyGainOffset").execute({
                "INPUT_RASTER": rst(mux), "GAIN": gain,
                "OFFSET": [0.0] * len(gain), "OUTPUT_RASTER_URI": mux_cal})
            check(mux_cal, "MUX定标")
            ensure_wavelength(mux_cal.replace(".dat", ".hdr"))
            log("-- QUAC --")
            mux_quac = os.path.join(out, "mux_quac.dat")
            engine.task("QUAC").execute({
                "INPUT_RASTER": rst(mux_cal), "OUTPUT_RASTER_URI": mux_quac})
            check(mux_quac, "QUAC")
            cleanup(mux_cal, mux_cal.replace(".dat", ".hdr"))
            mux_source = mux_quac
        else:
            log("-- 跳过定标/大气校正（几何模式）--")

        log("-- MUX 正射 8m NN --")
        mux_ortho = os.path.join(out, "mux_ortho.dat")
        engine.task("RPCOrthorectification").execute({
            "INPUT_RASTER": rst(mux_source), "DEM_RASTER": rst(DEM),
            "OUTPUT_COORDINATE_SYSTEM": coord_sys(zone),
            "OUTPUT_PIXEL_SIZE": [8.0, 8.0],
            "RESAMPLING": "Nearest Neighbor",
            "OUTPUT_RASTER_URI": mux_ortho})
        check(mux_ortho, "MUX正射")
        if mode == "full":
            cleanup(mux_quac, mux_quac.replace(".dat", ".hdr"))

        log("-- PAN 正射 2m Bilinear --")
        pan_ortho = os.path.join(out, "pan_ortho.dat")
        engine.task("RPCOrthorectification").execute({
            "INPUT_RASTER": rst(pan), "DEM_RASTER": rst(DEM),
            "OUTPUT_COORDINATE_SYSTEM": coord_sys(zone),
            "OUTPUT_PIXEL_SIZE": [2.0, 2.0],
            "RESAMPLING": "Bilinear",
            "OUTPUT_RASTER_URI": pan_ortho})
        check(pan_ortho, "PAN正射")

        log("-- NNDiffuse 融合 --")
        pansharp = os.path.join(out, "pansharp.dat")
        engine.task("NNDiffusePanSharpening").execute({
            "INPUT_LOW_RESOLUTION_RASTER": rst(mux_ortho),
            "INPUT_HIGH_RESOLUTION_RASTER": rst(pan_ortho),
            "OUTPUT_RASTER_URI": pansharp})
        check(pansharp, "融合")
        cleanup(pan_ortho, pan_ortho.replace(".dat", ".hdr"))

        log(f"########## {name} 完成 ##########")
        results.append((name, "OK"))
    except Exception as e:
        log(f"########## {name} 失败: {str(e)[:200]} ##########")
        results.append((name, f"FAIL"))

log("========== 全部结束 ==========")
for r in results:
    log(f"结果: {r[0]} -> {r[1]}")
if args.shutdown:
    log("60s 后关机")
    subprocess.call(["shutdown", "/s", "/t", "60", "/c", "GF6 batch done"])
else:
    log("未指定 --shutdown")
