# -*- coding: utf-8 -*-
"""
影像预处理状态检查工具
用法：python inspect_image.py <影像文件>
支持：ENVI 格式 (.dat/.hdr)，原始 tiff 给出形态判断
输出：关键元数据 + 像素值统计 + 处理阶段推断
"""
import os
import sys
import struct
import re

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DATA_TYPES = {1: ("Byte", "B", 1), 2: ("Int16", "h", 2), 3: ("Int32", "i", 4),
              4: ("Float32", "f", 4), 5: ("Float64", "d", 8), 6: ("Complex", "", 0),
              9: ("Int64", "q", 8), 12: ("UInt16", "H", 2), 13: ("UInt32", "I", 4),
              14: ("Int64", "q", 8), 15: ("UInt64", "Q", 8)}


def parse_hdr(hdr_path):
    """解析 ENVI .hdr 文本为 dict（支持 {} 多行块）。"""
    with open(hdr_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    hdr = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or "=" not in line:
            i += 1
            continue
        key, _, val = line.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if val.startswith("{"):
            block = [val[1:].strip()]
            i += 1
            while i < len(lines) and "}" not in lines[i]:
                block.append(lines[i].strip())
                i += 1
            if i < len(lines):
                block.append(lines[i].split("}")[0].strip())
            hdr[key] = " ".join(block)
        else:
            hdr[key] = val
        i += 1
    return hdr


def sample_pixels(dat_path, hdr):
    """在文件头/1/4/中部分别采样，合并统计（避开单点背景区）。"""
    ncols = int(hdr.get("samples", 0))
    nrows = int(hdr.get("lines", 0))
    nbands = int(hdr.get("bands", 0))
    dtype = int(hdr.get("data type", 4))
    interleave = hdr.get("interleave", "bsq").strip().lower()
    if dtype not in DATA_TYPES:
        return None
    name, fmt, size = DATA_TYPES[dtype]
    band_size = ncols * nrows * size
    # 取波段 1 的 25%/75% 行、波段 2 的 50% 行（避开边缘背景）
    offsets = [band_size // 4, band_size * 3 // 4, band_size + band_size // 2]
    vals = []
    with open(dat_path, "rb") as f:
        for off in offsets:
            f.seek(off)
            chunk = f.read(2048 * size)
            vals += struct.unpack("<" + fmt * (len(chunk) // size),
                                  chunk[: (len(chunk) // size) * size])
    finite = [v for v in vals if v == v]
    if not finite:
        return None
    mn, mx = min(finite), max(finite)
    is_int = all(float(v).is_integer() for v in finite[:300])
    return {"samples": ncols, "lines": nrows, "bands": nbands,
            "dtype": name, "interleave": interleave,
            "min": mn, "max": mx, "mean": sum(finite) / len(finite),
            "整数像素": is_int}


def infer(hdr, stats):
    """按特征推断处理阶段（优先级从末端到原始）。"""
    desc = hdr.get("description", "").lower()
    bands = hdr.get("band names", "").lower()
    mapinfo = hdr.get("map info", "").lower()
    has_wl = "wavelength" in hdr
    has_rpc = "rpc info" in hdr or "rpc" in hdr
    is_utm = "utm" in mapinfo
    is_geo = ("degree" in mapinfo or "degrees" in mapinfo
              or (mapinfo and "utm" not in mapinfo and "meters" not in mapinfo))
    if "nndiffuse" in desc:
        return "融合产品（处理链末端）— NNDiffuse 融合后"
    if "quac" in desc or (has_wl and stats and 0 <= stats["max"] <= 2):
        return "大气校正后（地表反射率，QUAC 输出）"
    if "orthorectified" in bands or is_utm:
        return "正射后（RPC 几何校正，UTM 投影）"
    if has_wl and has_rpc:
        return "辐射定标后（已补波长，未正射）"
    if has_rpc and not has_wl:
        return "原始 L1A 或仅定标（含 RPC，无波长）"
    return "无法确定，请对照说明文档手动判断"


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    path = os.path.abspath(sys.argv[1])
    if not os.path.exists(path):
        raise SystemExit(f"文件不存在: {path}")

    print("=" * 60)
    print(f"检查文件: {path}")
    print("=" * 60)

    ext = os.path.splitext(path)[1].lower()
    if ext == ".tiff" or ext == ".tif":
        # 原始 tiff：看同目录配套文件判断形态
        d = os.path.dirname(path)
        base = os.path.splitext(os.path.basename(path))[0]
        has_rpb = os.path.exists(os.path.join(d, base + ".rpb"))
        has_xml = os.path.exists(os.path.join(d, base + ".xml"))
        print(f"同目录配套: .rpb={has_rpb} .xml={has_xml}")
        if has_rpb and has_xml:
            print("→ 推断: 卫星原始分发包（L1A 未处理，tiff+rpb+xml 形态）")
        else:
            print("→ 推断: tiff 格式，非标准卫星分发形态，需进一步确认")
        return

    hdr_path = path if ext == ".hdr" else path.replace(".dat", ".hdr")
    if not os.path.exists(hdr_path):
        raise SystemExit(f"找不到 .hdr 文件: {hdr_path}")
    hdr = parse_hdr(hdr_path)

    print("\n【元数据】")
    print(f"  尺寸: {hdr.get('samples','?')} x {hdr.get('lines','?')} x {hdr.get('bands','?')} 波段")
    print(f"  数据类型: {hdr.get('data type','?')}  (interleave={hdr.get('interleave','?')})")
    print(f"  description: {hdr.get('description','(空)')[:80]}")
    print(f"  map info: {hdr.get('map info','(无)')[:100]}")
    print(f"  波长: {'有 ' + hdr.get('wavelength','')[:60] if 'wavelength' in hdr else '无'}")
    print(f"  RPC: {'有' if ('rpc info' in hdr or 'rpc' in hdr) else '无'}")
    print(f"  band names: {hdr.get('band names','(无)')[:80]}")

    dat_path = path if ext == ".dat" else hdr_path.replace(".hdr", ".dat")
    stats = sample_pixels(dat_path, hdr) if os.path.exists(dat_path) else None
    if stats:
        print("\n【像素值统计（前 4096 元素）】")
        print(f"  min={stats['min']:.4g}  max={stats['max']:.4g}  mean={stats['mean']:.4g}")
        print(f"  像素为整数: {stats['整数像素']}")

    print("\n【推断】")
    print("  → " + infer(hdr, stats))


if __name__ == "__main__":
    main()
