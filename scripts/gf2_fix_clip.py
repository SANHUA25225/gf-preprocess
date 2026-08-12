# -*- coding: utf-8 -*-
"""对已处理 GF-2 场景补充矢量裁剪（修复 OUTPUT_VECTOR_URI 参数 bug 后使用）"""
import os, sys, json, glob
from envipyengine import Engine

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = r"D:\aaaJZ\GF2_processed"
VEC_SRC = r"E:\金花茶保护区(1)\金花茶保护区\boundary.shp"

if not os.path.exists(VEC_SRC):
    print(f"矢量不存在: {VEC_SRC}")
    sys.exit(1)

def rst(p): return {"factory": "URLRaster", "url": p}
def vec(p): return {"factory": "URLVector", "url": p}

engine = Engine("ENVI")
scenes = sorted(glob.glob(os.path.join(BASE, "GF2_*")))
scenes = [s for s in scenes if os.path.isdir(s)]
print(f"找到 {len(scenes)} 个场景")

for scene in scenes:
    name = os.path.basename(scene)
    pansharp = os.path.join(scene, "pansharp.dat")
    mux_ortho = os.path.join(scene, "mux_ortho.dat")
    if not os.path.exists(pansharp):
        print(f"[跳过] {name}: 无 pansharp.dat")
        continue
    vec_utm = os.path.join(scene, "boundary_utm.shp")
    clip_dir = os.path.join(scene, "clip")

    # 从 hdr 获取 UTM zone
    hdr_path = mux_ortho.replace(".dat", ".hdr")
    zone = 48  # 默认
    if os.path.exists(hdr_path):
        with open(hdr_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "utm" in line.lower() and "zone" in line.lower():
                    import re
                    m = re.search(r'zone\s+(\d+)', line, re.IGNORECASE)
                    if m:
                        zone = int(m.group(1))
                        break
    print(f"{name}: UTM zone={zone}N, pansharp={os.path.getsize(pansharp)/1e9:.2f}GB")

    # 步骤: ReprojrectVector
    if not os.path.exists(vec_utm) or os.path.getsize(vec_utm) == 0:
        try:
            res = engine.task("ReprojectVector").execute({
                "INPUT_VECTOR": vec(VEC_SRC),
                "COORD_SYS": {"factory": "CoordSys", "coord_sys_code": 32600 + zone},
                "OUTPUT_VECTOR_URI": vec_utm,
            })
            print(f"  重投影 OK: {vec_utm}")
        except Exception as e:
            if os.path.exists(vec_utm) and os.path.getsize(vec_utm) > 0:
                print(f"  重投影 OK (忽略解析错误): {e}")
            else:
                print(f"  重投影 FAIL: {e}")
                continue

    # 步骤: SubsetRasterByVectorsDuBatch
    os.makedirs(clip_dir, exist_ok=True)
    before = set(os.listdir(clip_dir))
    try:
        engine.task("SubsetRasterByVectorsDuBatch").execute({
            "input_raster": rst(pansharp),
            "input_vectors": [vec(vec_utm)],
            "display_result": False,
            "output_dir": clip_dir,
        })
    except Exception as e:
        print(f"  裁剪执行异常（可能已成功）: {str(e)[:200]}")
    new_files = [f for f in set(os.listdir(clip_dir)) - before
                 if f.lower().endswith((".dat", ".tif"))]
    if new_files:
        clip_result = os.path.join(clip_dir, new_files[0])
        size_gb = os.path.getsize(clip_result) / 1e9
        print(f"  裁剪完成: {clip_result} ({size_gb:.2f} GB)")
    else:
        print(f"  裁剪: 未生成新文件（可能失败）")

print("完成")
