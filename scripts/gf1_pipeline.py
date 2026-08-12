# -*- coding: utf-8 -*-
"""
GF-1 PMS L1A 影像全流程处理脚本（ENVI 5.6 + envipyengine）
处理链：辐射定标 → QUAC 大气校正 → RPC+DEM 正射(UTM) → NNDiffuse 融合 → 矢量裁剪

用法：
    python gf1_pipeline.py <影像目录> <保护区矢量.shp> <输出目录>
示例：
    python gf1_pipeline.py "F:\\金花\\GF1_PMS2_xxx" "E:\\金花茶保护区(1)\\金花茶保护区\\boundary.shp" "F:\\金花\\GF1_processed"

说明：
    - 影像目录须含 -MSS*.tiff（多光谱）和 -PAN*.tiff（全色）及各自 .xml（含 AbsCeof 定标系数）
    - 定标系数从 xml 自动读取；波长按 GF-1 PMS 固定中心波长 485/555/660/830nm 写入
    - UTM 带号按影像中心经度自动计算
    - 每一步验证输出文件，失败即终止并报错
"""
import os
import sys
import json
import glob
import xml.etree.ElementTree as ET

from envipyengine import Engine

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------- 输入参数 ----------------
if len(sys.argv) != 4:
    print(__doc__)
    sys.exit(1)

SCENE_DIR = os.path.abspath(sys.argv[1])
VEC_SRC = os.path.abspath(sys.argv[2])
OUT = os.path.abspath(sys.argv[3])

DEM = r"C:\Program Files\ENVI\data\GMTED2010.jp2"   # ENVI 自带 DEM 路径（按你的环境修改）
GF1_WAVELENGTH = [485.0, 555.0, 660.0, 830.0]           # GF-1 PMS 中心波长 nm

# ---------------- 定位输入文件 ----------------
mss_list = sorted(glob.glob(os.path.join(SCENE_DIR, "*MSS*.tiff")))
pan_list = sorted(glob.glob(os.path.join(SCENE_DIR, "*PAN*.tiff")))
if not mss_list or not pan_list:
    raise SystemExit(f"影像目录中未找到 MSS/PAN tiff: {SCENE_DIR}")
MSS = mss_list[0]
PAN = pan_list[0]
print(f"多光谱: {MSS}\n全色:   {PAN}\n矢量:   {VEC_SRC}", flush=True)


def read_gain(tiff_path):
    """从同目录同名 .xml 读取 AbsCeof（L = AGain*DN + AOffset），返回增益列表。"""
    xml_path = os.path.splitext(tiff_path)[0] + ".xml"
    if not os.path.exists(xml_path):
        raise SystemExit(f"缺少元数据文件: {xml_path}")
    root = ET.parse(xml_path).getroot()
    gain = [float(x) for x in (root.findtext("AbsCeof") or "").split(",") if x.strip()]
    if not gain:
        raise SystemExit(f"{xml_path} 中未找到 AbsCeof 定标系数")
    return gain


def center_lon(tiff_path):
    """从 xml 读影像中心经度（用于 UTM 带号）。"""
    xml_path = os.path.splitext(tiff_path)[0] + ".xml"
    root = ET.parse(xml_path).getroot()
    return float(root.findtext("CenterLongitude"))


def utm_zone(lon):
    """经度 → UTM 带号（WGS84）。"""
    return int((lon + 180) / 6) + 1


def ensure_wavelength(hdr_path, wave_list):
    """定标输出丢失波长信息（QUAC 必需），若无则追加到 .hdr。"""
    with open(hdr_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    if "wavelength" in content.lower():
        return
    block = ("\nwavelength = {\n " + ", ".join(f"{w:.6f}" for w in wave_list) +
             "}\nwavelength units = Nanometers\n")
    with open(hdr_path, "a", encoding="utf-8") as f:
        f.write(block)
    print(f"已写入波长信息: {hdr_path}", flush=True)


# ---------------- 工具函数 ----------------
def rst(path):
    return {"factory": "URLRaster", "url": path}


def vec(path):
    return {"factory": "URLVector", "url": path}


def coord_sys(zone):
    return {"factory": "CoordSys", "coord_sys_code": 32600 + zone}   # 326xx = WGS84 UTM N


def check(path, tag):
    ok = os.path.exists(path) and os.path.getsize(path) > 0
    size = os.path.getsize(path) if os.path.exists(path) else 0
    print(f"[{tag}] {'OK' if ok else 'FAIL'} {path} ({size} bytes)", flush=True)
    if not ok:
        raise RuntimeError(f"{tag} 输出缺失或为空: {path}")


def dump_json(obj, tag):
    print(f"[{tag}] {json.dumps(obj, ensure_ascii=False)[:400]}", flush=True)


def safe_execute(task, params, tag, check_path=None):
    """执行任务；ReprojectVector 等任务 stdout 非标准 JSON 时容错（产物已生成）。"""
    try:
        res = task.execute(params)
        dump_json(res, tag)
    except Exception as e:
        if check_path and os.path.exists(check_path) and os.path.getsize(check_path) > 0:
            print(f"[{tag}] 任务完成但结果解析异常（忽略）: {str(e)[:120]}", flush=True)
        else:
            raise
    if check_path:
        check(check_path, tag)


# ---------------- 主流程 ----------------
os.makedirs(OUT, exist_ok=True)
zone = utm_zone(center_lon(MSS))
print(f"UTM 带号: {zone}N  (EPSG:{32600 + zone})", flush=True)
engine = Engine("ENVI")

# 步骤 1: MSS 辐射定标
print("== 步骤 1/7: MSS 辐射定标 ==", flush=True)
mss_gain = read_gain(MSS)
mss_cal = os.path.join(OUT, "mss_cal.dat")
safe_execute(engine.task("ApplyGainOffset"), {
    "INPUT_RASTER": rst(MSS),
    "GAIN": mss_gain,
    "OFFSET": [0.0] * len(mss_gain),
    "OUTPUT_RASTER_URI": mss_cal,
}, "MSS定标", mss_cal)
ensure_wavelength(mss_cal.replace(".dat", ".hdr"), GF1_WAVELENGTH)

# 步骤 2: PAN 辐射定标
print("== 步骤 2/7: PAN 辐射定标 ==", flush=True)
pan_gain = read_gain(PAN)
pan_cal = os.path.join(OUT, "pan_cal.dat")
safe_execute(engine.task("ApplyGainOffset"), {
    "INPUT_RASTER": rst(PAN),
    "GAIN": pan_gain,
    "OFFSET": [0.0] * len(pan_gain),
    "OUTPUT_RASTER_URI": pan_cal,
}, "PAN定标", pan_cal)

# 步骤 3: QUAC 大气校正（4 波段多光谱，用默认 Generic 传感器）
print("== 步骤 3/7: QUAC 大气校正 ==", flush=True)
mss_quac = os.path.join(OUT, "mss_quac.dat")
safe_execute(engine.task("QUAC"), {
    "INPUT_RASTER": rst(mss_cal),
    "OUTPUT_RASTER_URI": mss_quac,
}, "QUAC", mss_quac)

# 步骤 4: MSS RPC 正射
print("== 步骤 4/7: MSS RPC 正射 ==", flush=True)
mss_ortho = os.path.join(OUT, "mss_ortho.dat")
safe_execute(engine.task("RPCOrthorectification"), {
    "INPUT_RASTER": rst(mss_quac),
    "DEM_RASTER": rst(DEM),
    "OUTPUT_COORDINATE_SYSTEM": coord_sys(zone),
    "OUTPUT_PIXEL_SIZE": [8.0, 8.0],
    "RESAMPLING": "Cubic Convolution",
    "OUTPUT_RASTER_URI": mss_ortho,
}, "MSS正射", mss_ortho)

# 步骤 5: PAN RPC 正射
print("== 步骤 5/7: PAN RPC 正射 ==", flush=True)
pan_ortho = os.path.join(OUT, "pan_ortho.dat")
safe_execute(engine.task("RPCOrthorectification"), {
    "INPUT_RASTER": rst(pan_cal),
    "DEM_RASTER": rst(DEM),
    "OUTPUT_COORDINATE_SYSTEM": coord_sys(zone),
    "OUTPUT_PIXEL_SIZE": [2.0, 2.0],
    "RESAMPLING": "Cubic Convolution",
    "OUTPUT_RASTER_URI": pan_ortho,
}, "PAN正射", pan_ortho)

# 步骤 6: NNDiffuse 融合
print("== 步骤 6/7: NNDiffuse 融合 ==", flush=True)
pansharp = os.path.join(OUT, "pansharp.dat")
safe_execute(engine.task("NNDiffusePanSharpening"), {
    "INPUT_LOW_RESOLUTION_RASTER": rst(mss_ortho),
    "INPUT_HIGH_RESOLUTION_RASTER": rst(pan_ortho),
    "OUTPUT_RASTER_URI": pansharp,
}, "融合", pansharp)

# 步骤 7: 矢量重投影（CGCS2000 → 影像坐标系 WGS84 UTM）+ 裁剪
print("== 步骤 7/7: 矢量重投影 + 裁剪 ==", flush=True)
vec_utm = os.path.join(OUT, "boundary_utm.shp")
safe_execute(engine.task("ReprojectVector"), {
    "INPUT_VECTOR": vec(VEC_SRC),
    "COORD_SYS": coord_sys(zone),
    "OUTPUT_VECTOR_URI": vec_utm,
}, "矢量重投影", vec_utm)

clip_dir = os.path.join(OUT, "clip")
os.makedirs(clip_dir, exist_ok=True)
before = set(os.listdir(clip_dir))
safe_execute(engine.task("SubsetRasterByVectorsDuBatch"), {
    "input_raster": rst(pansharp),
    "input_vectors": [vec(vec_utm)],
    "display_result": False,
    "output_dir": clip_dir,
}, "矢量裁剪")
new = [f for f in set(os.listdir(clip_dir)) - before
       if f.lower().endswith((".dat", ".tif"))]
if not new:
    raise RuntimeError("裁剪步骤未找到输出文件")
clipped = os.path.join(clip_dir, new[0])
check(clipped, "裁剪结果")

print("== 全部完成 ==", flush=True)
print(f"融合结果: {pansharp}", flush=True)
print(f"裁剪结果: {clipped}", flush=True)
