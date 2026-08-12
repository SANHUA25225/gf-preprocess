# -*- coding: utf-8 -*-
"""
GF-2 PMS L1A 批量预处理（定标→QUAC→RPC正射→NNDiffuse融合→矢量裁剪）
适配 PMS1 (MSS1+PAN1) 和 PMS2 (MSS2+PAN2) 两种传感器

用法：
    python gf2_batch.py [--limit N] [--skip N] [--shutdown]

处理链（full 模式，有定标系数）：
    ① MSS AbsCeof 定标 → ①' 补波长(.hdr) → ② QUAC → ③ MUX正射(3.2m,NN)
    → ④ PAN 正射(0.8m,原始DN,Bilinear) → ⑤ NNDiffuse融合 → ⑥ 矢量裁剪

geo 模式（缺定标系数）：跳过①-②，直接从③开始
"""
import os
import sys
import json
import time
import glob
import shutil
import subprocess
import argparse
import xml.etree.ElementTree as ET

from envipyengine import Engine

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

parser = argparse.ArgumentParser(description="GF-2 PMS L1A 批量预处理 + 矢量裁剪")
parser.add_argument("--limit", type=int, default=0, help="最多处理 N 景（0=全部）")
parser.add_argument("--skip", type=int, default=0, help="跳过前 N 景")
parser.add_argument("--shutdown", action="store_true", help="全部完成后自动关机")
args = parser.parse_args()

# ====== 配置（按实际环境修改） ======
BASE = r"D:\aaaJZ\GF2\extracted"                     # 解压后场景所在目录
OUT_BASE = r"D:\aaaJZ\GF2_processed"                  # 输出目录
VEC_SRC = r"E:\金花茶保护区(1)\金花茶保护区\boundary.shp"  # 裁剪矢量
DEM = r"D:\ENVI 5.6(64bit)\ENVI56\data\GMTED2010.jp2"  # DEM 路径
WAVELENGTH = [485.0, 555.0, 660.0, 830.0]              # GF-2 PMS 中心波长 nm
MUX_GSD = 3.2                                          # 多光谱正射像元 (原始 3.24m)
PAN_GSD = 0.8                                          # 全色正射像元 (原始 0.81m)

os.makedirs(OUT_BASE, exist_ok=True)
LOG = os.path.join(OUT_BASE, "gf2_batch_log.txt")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_gain(tiff_path):
    """从同目录同名 .xml 读取 AbsCeof（GF-2 定标字段）。"""
    xml_path = os.path.splitext(tiff_path)[0] + ".xml"
    root = ET.parse(xml_path).getroot()
    text = root.findtext("AbsCeof") or ""
    gain = [float(x) for x in text.split(",") if x.strip()]
    if not gain:
        raise RuntimeError(f"{xml_path} 无有效 AbsCeof")
    return gain


def utm_zone(tiff_path):
    """UTM 带号（多级降级）：xml ZoneNo → CenterLongitude → 角点平均 → 文件名解析。"""
    xml_path = os.path.splitext(tiff_path)[0] + ".xml"
    root = ET.parse(xml_path).getroot()
    # 1) ZoneNo
    zone = root.findtext("ZoneNo")
    if zone and zone.strip():
        return int(float(zone))
    # 2) CenterLongitude
    lon = root.findtext("CenterLongitude")
    if lon and lon.strip():
        return int((float(lon) + 180) / 6) + 1
    # 3) 四个角点经度取平均
    corners = ['TopLeftLongitude', 'TopRightLongitude',
               'BottomRightLongitude', 'BottomLeftLongitude']
    lons = []
    for c in corners:
        v = root.findtext(c)
        if v and v.strip():
            lons.append(float(v))
    if lons:
        return int((sum(lons) / len(lons) + 180) / 6) + 1
    # 4) 文件名 E 经度标签
    import re
    name = os.path.basename(os.path.dirname(tiff_path))
    m = re.search(r'E(\d+\.?\d*)', name)
    if m:
        return int((float(m.group(1)) + 180) / 6) + 1
    raise RuntimeError(f"无法确定 UTM 带号: {xml_path}")


def rst(path):
    return {"factory": "URLRaster", "url": path}


def vec(path):
    return {"factory": "URLVector", "url": path}


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
            try:
                os.remove(p)
                log(f"[清理] 删除 {p}")
            except Exception as e:
                log(f"[清理] 删除失败 {p}: {e}")


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


def safe_execute(task, params, tag, check_path=None):
    """执行任务；ReprojectVector 等 stdout 非 JSON 时容错。"""
    try:
        res = task.execute(params)
        log(f"[{tag}] 返回: {json.dumps(res, ensure_ascii=False)[:200]}")
    except Exception as e:
        if check_path and os.path.exists(check_path) and os.path.getsize(check_path) > 0:
            log(f"[{tag}] 任务完成但结果解析异常（忽略）: {str(e)[:120]}")
        else:
            raise
    if check_path:
        check(check_path, tag)


# ====== 扫描场景 ======
scenes = sorted(glob.glob(os.path.join(BASE, "GF2_PMS*_E*")))
scenes = [s for s in scenes if os.path.isdir(s)]
if args.skip:
    scenes = scenes[args.skip:]
if args.limit:
    scenes = scenes[:args.limit]
log(f"发现 {len(scenes)} 个场景待处理:")
for s in scenes:
    log(f"  {os.path.basename(s)}")
if not scenes:
    log("未找到场景目录，请检查 BASE 路径和解压状态")
    sys.exit(1)

engine = Engine("ENVI")
results = []

for scene in scenes:
    name = os.path.basename(scene)
    out = os.path.join(OUT_BASE, name)
    # 跳过已完成的场景（clip 目录下已有裁剪产品）
    clip_check = os.path.join(out, "clip", "pansharp_boundary_utm.dat")
    if os.path.exists(clip_check) and os.path.getsize(clip_check) > 0:
        log(f"跳过已完成: {name} (已有 {clip_check})")
        results.append((name, "SKIPPED"))
        continue
    log(f"{'#'*10} 开始: {name} {'#'*10}")
    try:
        # 定位 MSS 和 PAN tiff（兼容 MSS1/MSS2 和 PAN1/PAN2 命名）
        mss_candidates = glob.glob(os.path.join(scene, "*MSS*.tiff"))
        pan_candidates = glob.glob(os.path.join(scene, "*PAN*.tiff"))
        if not mss_candidates or not pan_candidates:
            raise RuntimeError(f"找不到 MSS/PAN tiff 文件: {scene}")
        mux = mss_candidates[0]
        pan = pan_candidates[0]
        log(f"MSS: {os.path.basename(mux)}  PAN: {os.path.basename(pan)}")

        os.makedirs(out, exist_ok=True)
        zone = utm_zone(mux)
        log(f"UTM zone: {zone}N (EPSG:{32600 + zone})")

        # 定标系数检测
        try:
            gain = read_gain(mux)
            mode = "full"
            log(f"AbsCeof: {gain} -> 完整模式（定标+大气校正）")
        except Exception as e:
            gain = None
            mode = "geo"
            log(f"无有效定标系数 ({str(e)[:80]}) -> 几何模式")

        mux_source = mux
        if mode == "full":
            # ① 多光谱辐射定标
            log("-- ① MSS 辐射定标 --")
            mux_cal = os.path.join(out, "mux_cal.dat")
            safe_execute(engine.task("ApplyGainOffset"), {
                "INPUT_RASTER": rst(mux),
                "GAIN": gain,
                "OFFSET": [0.0] * len(gain),
                "OUTPUT_RASTER_URI": mux_cal,
            }, "MSS定标", mux_cal)
            ensure_wavelength(mux_cal.replace(".dat", ".hdr"))

            # ② QUAC 大气校正
            log("-- ② QUAC 大气校正 --")
            mux_quac = os.path.join(out, "mux_quac.dat")
            safe_execute(engine.task("QUAC"), {
                "INPUT_RASTER": rst(mux_cal),
                "OUTPUT_RASTER_URI": mux_quac,
            }, "QUAC", mux_quac)
            cleanup(mux_cal, mux_cal.replace(".dat", ".hdr"))
            mux_source = mux_quac
        else:
            log("-- 跳过 ①②（几何模式）--")

        # ③ MUX 正射（3.2m, Nearest Neighbor）
        log("-- ③ MUX RPC 正射 (3.2m, NN) --")
        mux_ortho = os.path.join(out, "mux_ortho.dat")
        safe_execute(engine.task("RPCOrthorectification"), {
            "INPUT_RASTER": rst(mux_source),
            "DEM_RASTER": rst(DEM),
            "OUTPUT_COORDINATE_SYSTEM": coord_sys(zone),
            "OUTPUT_PIXEL_SIZE": [MUX_GSD, MUX_GSD],
            "RESAMPLING": "Nearest Neighbor",
            "OUTPUT_RASTER_URI": mux_ortho,
        }, "MUX正射", mux_ortho)
        if mode == "full":
            cleanup(mux_quac, mux_quac.replace(".dat", ".hdr"))

        # ④ PAN 正射（0.8m, 原始 DN, Bilinear）
        log("-- ④ PAN RPC 正射 (0.8m, 原始DN, Bilinear) --")
        pan_ortho = os.path.join(out, "pan_ortho.dat")
        safe_execute(engine.task("RPCOrthorectification"), {
            "INPUT_RASTER": rst(pan),
            "DEM_RASTER": rst(DEM),
            "OUTPUT_COORDINATE_SYSTEM": coord_sys(zone),
            "OUTPUT_PIXEL_SIZE": [PAN_GSD, PAN_GSD],
            "RESAMPLING": "Bilinear",
            "OUTPUT_RASTER_URI": pan_ortho,
        }, "PAN正射", pan_ortho)

        # ⑤ NNDiffuse 融合
        log("-- ⑤ NNDiffuse 融合 --")
        pansharp = os.path.join(out, "pansharp.dat")
        safe_execute(engine.task("NNDiffusePanSharpening"), {
            "INPUT_LOW_RESOLUTION_RASTER": rst(mux_ortho),
            "INPUT_HIGH_RESOLUTION_RASTER": rst(pan_ortho),
            "OUTPUT_RASTER_URI": pansharp,
        }, "融合", pansharp)
        cleanup(pan_ortho, pan_ortho.replace(".dat", ".hdr"))

        # ⑥ 矢量裁剪
        log("-- ⑥ 矢量裁剪 --")
        if os.path.exists(VEC_SRC):
            vec_utm = os.path.join(out, "boundary_utm.shp")
            safe_execute(engine.task("ReprojectVector"), {
                "INPUT_VECTOR": vec(VEC_SRC),
                "COORD_SYS": coord_sys(zone),
                "OUTPUT_VECTOR_URI": vec_utm,
            }, "矢量重投影", vec_utm)

            clip_dir = os.path.join(out, "clip")
            os.makedirs(clip_dir, exist_ok=True)
            before = set(os.listdir(clip_dir))
            safe_execute(engine.task("SubsetRasterByVectorsDuBatch"), {
                "input_raster": rst(pansharp),
                "input_vectors": [vec(vec_utm)],
                "display_result": False,
                "output_dir": clip_dir,
            }, "矢量裁剪")
            new_files = [f for f in set(os.listdir(clip_dir)) - before
                         if f.lower().endswith((".dat", ".tif"))]
            if new_files:
                clip_result = os.path.join(clip_dir, new_files[0])
                check(clip_result, "裁剪结果")
                # 裁剪成功→删除全幅 pansharp 节省空间（保留裁剪版即可）
                cleanup(pansharp, pansharp.replace(".dat", ".hdr"),
                        pansharp.replace(".dat", ".dat.enp"))
        else:
            log(f"[裁剪] 矢量不存在，跳过: {VEC_SRC}")

        log(f"{'#'*10} {name} 完成 {'#'*10}")
        log(f"  融合产品: {pansharp}")
        log(f"  多光谱正射: {mux_ortho}")
        results.append((name, "OK"))

    except Exception as e:
        log(f"{'#'*10} {name} 失败: {str(e)[:500]} {'#'*10}")
        results.append((name, f"FAIL: {str(e)[:200]}"))

log("========== 全部结束 ==========")
for r in results:
    status = "OK" if r[1] == "OK" else "FAIL"
    log(f"  [{status}] {r[0]}")
ok_count = sum(1 for r in results if r[1] == "OK")
log(f"成功: {ok_count}/{len(results)}")
if args.shutdown:
    log("60s 后关机...")
    try:
        subprocess.call(["shutdown", "/s", "/t", "60", "/c", "GF2 batch done"])
    except Exception as e:
        log(f"关机命令失败: {e}")
else:
    log("未指定 --shutdown，不关机")
