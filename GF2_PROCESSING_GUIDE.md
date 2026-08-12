# GF-2 PMS L1A 数据预处理操作文档

> 处理日期：2026-08-12 | 数据范围：18景 | 18/18 全部成功

---

## 一、处理目标

将 **18景 GF-2 PMS L1A 原始数据**（国产高分二号卫星，多光谱 3.24m + 全色 0.81m）预处理为可直接使用的 **L2 级产品**：

| 最终产品 | 规格 | 用途 |
|---|---|---|
| `clip/pansharp_boundary_utm.dat` | **0.8m** 分辨率，4波段，UTM投影，按保护区矢量裁剪 | 面向对象分类、目视解译 |
| `mux_ortho.dat` | **3.2m** 分辨率，4波段，UTM投影 | 像元级光谱分类、植被指数 |

处理链：`辐射定标 → 大气校正 → RPC+DEM正射 → NNDiffuse融合 → 矢量裁剪`

---

## 二、环境要求

| 组件 | 路径/版本 | 作用 |
|---|---|---|
| Python | 3.11.9 (`...\Python311\python.exe`) | envipyengine 宿主 |
| envipyengine | 1.0.9 | 驱动 ENVI taskengine 执行处理任务 |
| ENVI taskengine | `D:\ENVI 5.6(64bit)\ENVI56\IDL88\bin\bin.x86_64\taskengine.exe` | 实际执行辐射定标/大气校正/正射/融合 |
| GMTED2010 DEM | `D:\ENVI 5.6(64bit)\ENVI56\data\GMTED2010.jp2` | RPC 正射校正的高程参考 |
| 矢量边界 | `E:\金花茶保护区(1)\金花茶保护区\boundary.shp` | 裁剪范围 |

---

## 三、数据源

### 3.1 原始数据概况

```
D:\aaaJZ\GF2\
├── GF2_PMS1_E108.0_N21.7_20250402_L1A14547899001.tar.gz   (PMS1相机 × 12景)
├── GF2_PMS1_E108.0_N21.9_20150114_L1A0000588349.tar.gz
├── ...
├── GF2_PMS2_E108.0_N21.7_20241120_L1A14261574001.tar.gz   (PMS2相机 × 6景)
└── ...
```

- **卫星/传感器**：GF-2 PMS1（12景）+ PMS2（6景）
- **级别**：L1A（原始DN值 + RPC有理多项式系数）
- **时相**：2014–2026年，横跨12年
- **格式**：每景一个 `.tar.gz` 压缩包（1–1.4 GB）

### 3.2 压缩包内部结构

```
GF2_PMS1_E108.0_N21.7_20250402_L1A14547899001.tar.gz
├── GF2_PMS1_...-MSS1.tiff       # 多光谱（7300×6920，4波段，3.24m）
├── GF2_PMS1_...-MSS1.xml        # 多光谱元数据（含 AbsCeof 定标系数）
├── GF2_PMS1_...-MSS1.rpb        # 多光谱RPC模型
├── GF2_PMS1_...-PAN1.tiff       # 全色（29200×27680，1波段，0.81m）
├── GF2_PMS1_...-PAN1.xml        # 全色元数据
├── GF2_PMS1_...-PAN1.rpb        # 全色RPC模型
├── GF2_PMS1_....xml             # 总元数据
└── *.jpg                         # 缩略图
```

> PMS2 相机的命名后缀为 `-MSS2.tiff` / `-PAN2.tiff`，与 PMS1 不同。

### 3.3 GF-2 传感器参数

| 参数 | 多光谱 (MSS) | 全色 (PAN) |
|---|---|---|
| 像元尺寸 | **3.24m** | **0.81m** |
| 波段数 | 4 | 1 |
| 定标字段 | `AbsCeof` | `AbsCeof` |
| UTM 带号 | 需计算（ZoneNo 常为空） | 同左 |
| 数据差异 | 2023+ xml 含 CenterLongitude；老数据仅含角点经纬度 | 同左 |

---

## 四、处理步骤详解

### 步骤 1：解压数据

**目的**：将 tar.gz 解压为脚本可遍历的目录结构。

```bash
mkdir -p D:\aaaJZ\GF2\extracted

for f in D:\aaaJZ\GF2\*.tar.gz; do
    name=$(basename "$f" .tar.gz)
    mkdir -p "D:\aaaJZ\GF2\extracted\$name"
    tar -xzf "$f" -C "D:\aaaJZ\GF2\extracted\$name"
done
```

**结果**：18个子目录，共 35GB，每景含 MSS tiff + PAN tiff + xml + rpb。

---

### 步骤 2：创建 GF-2 处理脚本 `gf2_batch.py`

**目的**：GF-2 是此前的未实测卫星，没有现成脚本。需要参照已有卫星脚本创建专用处理程序。

**参考来源**：
- `gf1_pipeline.py` — AbsCeof 定标逻辑（GF-2 与 GF-1 使用相同的定标字段）
- `gf7_batch.py` — 批量处理 + PAN 原始DN正射 + full/geo 自适应 + 中间产物清理

**GF-2 专用配置**：
```python
DEM = r"D:\ENVI 5.6(64bit)\ENVI56\data\GMTED2010.jp2"
WAVELENGTH = [485.0, 555.0, 660.0, 830.0]  # PMS 传感器中心波长(nm)
MUX_GSD = 3.2   # 多光谱正射像元（原始3.24m→设定3.2m）
PAN_GSD = 0.8   # 全色正射像元（原始0.81m→设定0.8m）
VEC_SRC = r"E:\金花茶保护区(1)\金花茶保护区\boundary.shp"
```

**处理流程**（6步）：

```
MSS tiff ──① ApplyGainOffset ──> ①' 补波长 ──② QUAC ──>
③ RPC+DEM正射(3.2m, NN) ──> ─┐
                                ├──⑤ NNDiffuse融合 ──> ⑥ 矢量裁剪
PAN tiff ──④ RPC+DEM正射(0.8m, Bilinear, 原始DN) ──┘
```

**关键设计决策**：

| 决策 | 理由 |
|---|---|
| PAN 用原始 DN 正射（不定标） | 节省约 17GB Float64 中间产物；NNDiffuse 内部归一化兼容混合输入 |
| 每步完成后立即清理上一步产物 | D盘空间紧张（仅120G可用），18景全部处理约需200G峰值空间 |
| full/geo 模式自适应 | 老数据缺 AbsCeof → 自动降级为几何模式（仅正射+融合） |
| 裁剪后删除全幅 pansharp | 最终产物仅 1–2GB/景，全幅融合 ~9GB 可丢弃 |

---

### 步骤 3：试点处理（第1景）

**目的**：先跑1景验证全流程正确，避免批量处理时系统性错误浪费数小时。

```bash
python gf2_batch.py --limit 1
```

**试点景**：`GF2_PMS1_E108.0_N21.7_20250402_L1A14547899001`

**每步耗时与产出**：

| 步骤 | 耗时 | 产出文件 | 大小 | 说明 |
|---|---|---|---|---|
| ① MSS 辐射定标 | 9秒 | `mux_cal.dat` (Float64) | 1.62 GB | DN → 辐亮度，AbsCeof=[0.1374,0.1784,0.1723,0.1894] |
| ①' 补波长 | <1秒 | `.hdr` 追加2行 | — | ENVI定标输出丢波长，QUAC 必需此信息 |
| ② QUAC 大气校正 | 95秒 | `mux_quac.dat` (Float32) | 0.40 GB | 辐亮度 → 地表反射率（0–1量级），删除定标产物 |
| ③ MUX RPC 正射 | 23秒 | `mux_ortho.dat` | 0.55 GB | 3.2m, Nearest Neighbor（保持光谱），删除QUAC产物 |
| ④ PAN RPC 正射 | 201秒 | `pan_ortho.dat` | 2.21 GB | 0.8m, Bilinear, 原始DN（不定标） |
| ⑤ NNDiffuse 融合 | 289秒 | `pansharp.dat` | 8.82 GB | 0.8m 4波段融合，删除PAN正射 |
| ⑥ 矢量裁剪 | 17秒 | `clip/pansharp_boundary_utm.dat` | **1.06 GB** | 按 boundary.shp 裁剪，删除全幅 pansharp |

**首景总耗时：约 10 分钟**

**遇到的问题 #1：矢量裁剪参数格式错误**

```
Parameter OUTPUT_VECTOR_URI failed hydration:
Invalid input, expect a scalar string
```

**根因**：`ReprojectVector` 任务的 `OUTPUT_VECTOR_URI` 参数与其他任务不同——它接受**字符串路径**而非 `{"factory":"URLVector","url":"..."}` 对象。

**修复**：
```python
# 错误 ❌
"OUTPUT_VECTOR_URI": vec(vec_utm)   # vec() 包装了 URLVector 对象

# 正确 ✅
"OUTPUT_VECTOR_URI": vec_utm        # 直接传字符串路径
```

用修复脚本 `gf2_fix_clip.py` 对试点补充裁剪，裁剪后仅 1.06GB（全幅 8.82GB → 裁剪 1.06GB，缩减 88%）。

**验证**（`inspect_image.py`）：
```
尺寸: 9874 × 13474 × 4 波段
像元: 0.80m ✅
投影: UTM Zone 48N WGS-84 ✅
波长: 485, 555, 660, 830 nm ✅
推断: 融合产品（处理链末端） ✅
```

---

### 步骤 4：批量处理（第1批，16景）

**目的**：批量处理剩余数据。

```bash
python gf2_batch.py --skip 1    # 跳过首景试点
```

**结果**：8景成功，8景失败。

**成功**（2023+ 新数据，有 AbsCeof → full 模式）：8景

**失败**（2014–2022 老数据）：8景，全部报错：
```
float() argument must be a string or a real number, not 'NoneType'
```

---

### 步骤 5：诊断并修复老数据失败

**根因分析**：提取失败景 xml，发现老 xml 缺少 `CenterLongitude` 字段：

```xml
<!-- 新 xml 有这些字段 -->
<CenterLatitude>21.724386</CenterLatitude>
<CenterLongitude>107.979752</CenterLongitude>

<!-- 老 xml 没有 CenterLatitude/CenterLongitude，只有四个角点 -->
<TopLeftLatitude>21.8527</TopLeftLatitude>
<TopLeftLongitude>107.968</TopLeftLongitude>
<TopRightLongitude>108.223</TopRightLongitude>
<BottomRightLongitude>108.177</BottomRightLongitude>
<BottomLeftLongitude>107.923</BottomLeftLongitude>
<!-- 也没有 AbsCeof -->
```

`utm_zone()` 函数的 `float(root.findtext("CenterLongitude"))` → `float(None)` → 崩溃。

**修复：增加 4 级降级策略**

```python
def utm_zone(tiff_path):
    """UTM 带号（多级降级）：ZoneNo → CenterLongitude → 角点平均 → 文件名"""
    # 1) 直接读取 UTM 带号
    zone = root.findtext("ZoneNo")
    if zone and zone.strip():
        return int(float(zone))

    # 2) 中心经度计算
    lon = root.findtext("CenterLongitude")
    if lon and lon.strip():
        return int((float(lon) + 180) / 6) + 1

    # 3) 四个角点经度取平均（兼容老数据）
    corners = ['TopLeftLongitude', 'TopRightLongitude',
               'BottomRightLongitude', 'BottomLeftLongitude']
    lons = [float(root.findtext(c)) for c in corners if root.findtext(c)]
    if lons:
        return int((sum(lons) / len(lons) + 180) / 6) + 1

    # 4) 文件名 E 标签兜底
    m = re.search(r'E(\d+\.?\d*)', filename)
    if m:
        return int((float(m.group(1)) + 180) / 6) + 1

    raise RuntimeError("无法确定 UTM 带号")
```

**同步优化**：添加跳过已完成逻辑，避免重跑成功的9景：

```python
out = os.path.join(OUT_BASE, name)
clip_check = os.path.join(out, "clip", "pansharp_boundary_utm.dat")
if os.path.exists(clip_check) and os.path.getsize(clip_check) > 0:
    log(f"跳过已完成: {name}")
    continue
```

---

### 步骤 6：重跑老数据（第2批）

```bash
# 清理第一批失败景的空目录
rmdir D:\aaaJZ\GF2_processed\GF2_PMS1_E108.1_N21.7_20220503_...  # 8个空目录

# 重跑（自动跳过有产品的9景，仅处理剩余9景）
python gf2_batch.py
```

**结果**：9景新增成功（全部 geo 模式），9景跳过。

> **geo 模式说明**：老数据 xml 同时缺失 `AbsCeof`（定标系数）和 `CenterLongitude`。AbsCeof 缺失 → 无法辐射定标和大气校正 → 自动降级 geo 模式（仅正射+融合，输出原始 DN 而非反射率）。CenterLongitude 缺失 → 通过角点经纬度降级计算（已修复）。

---

## 五、最终产品

### 5.1 目录结构

```
D:\aaaJZ\GF2_processed\              (18个子目录)
├── GF2_PMS1_E108.0_N21.7_20250402/  (full模式)
│   ├── mux_ortho.dat                 # 3.2m 多光谱正射（QUAC地表反射率，有波长）
│   ├── mux_ortho.hdr
│   ├── boundary_utm.shp              # 重投影后的矢量
│   └── clip/
│       └── pansharp_boundary_utm.dat # 0.8m 裁剪融合（0.8m, 4波段, 有波长）
├── GF2_PMS1_E108.3_N21.7_20150119/  (geo模式)
│   ├── mux_ortho.dat                 # 3.2m 多光谱正射（原始DN，无波长）
│   └── clip/
│       └── pansharp_boundary_utm.dat # 0.8m 裁剪融合（0.8m, 4波段, 无波长）
└── ...
```

### 5.2 统计数据

| 指标 | 数值 |
|---|---|
| 成功景数 | **18 / 18** |
| full 模式（定标+QUAC大气校正） | 9 景（2023+ 数据） |
| geo 模式（仅正射+融合） | 9 景（2014–2022 数据） |
| 裁剪融合总大小 | **24 GB** |
| 多光谱正射总大小 | **~10 GB** |
| 单景耗时 | ~10分钟(full) / ~7分钟(geo) |
| 总处理耗时 | ~3小时 |
| D盘最终剩余 | 154 GB |

### 5.3 产品验证

| 检查项 | full模式结果 | geo模式结果 |
|---|---|---|
| 像元尺寸 | 0.80m ✅ | 0.80m ✅ |
| 波段数 | 4 ✅ | 4 ✅ |
| 投影 | UTM 48N/49N WGS-84 ✅ | UTM 48N/49N WGS-84 ✅ |
| 处理阶段标签 | NNDiffuse 融合 ✅ | NNDiffuse 融合 ✅ |
| 波长信息 | 485/555/660/830 nm ✅ | 无（预期）⚠️ |
| 像素值范围 | 0~6230 (UInt16 DN) | 0~767 (UInt16 DN) |

---

## 六、遇到的问题与解决

| # | 问题 | 根因 | 解决 |
|---|---|---|---|
| 1 | 矢量裁剪报参数格式错误 | `ReprojectVector` 的 `OUTPUT_VECTOR_URI` 只接受字符串路径 | 不包装为 URLVector 对象 |
| 2 | 老数据 UTM 带号计算崩溃 | xml 缺少 `CenterLongitude`，`float(None)` 抛异常 | 增加角点经纬度平均 + 文件名解析降级 |
| 3 | 老数据无定标系数 | xml 同时缺少 `AbsCeof` | 自动降级 geo 模式 |
| 4 | D盘空间不足（仅120G） | 全幅融合 ~9GB/景，18景总计需 >200G | 裁剪后删全幅 + 逐景清理中间产物 |
| 5 | GF-2 命名与现有卫星均不同 | `-MSS1`/`-MSS2` 而非 `-MUX`/`-MSS`/`-BWDMUX` | 用通配符 `*MSS*.tiff` 匹配 |

---

## 七、脚本部署

| 脚本 | 用途 |
|---|---|
| `gf2_batch.py` | GF-2 批量处理主脚本 |
| `gf2_fix_clip.py` | 对已有融合结果补充矢量裁剪 |

部署路径：`.reasonix/skills/gf-preprocess/scripts/`

---

## 八、后续建议

1. **解压数据清理**：确认产品无误后，可删除 `D:\aaaJZ\GF2\extracted\`（35GB）。
2. **geo 模式补波长**：如需为 geo 模式产品添加波长信息，可从同区域 full 模式景的 `.hdr` 中复制 wavelength 字段（4行文本）。
3. **PMS1/PMS2 差异**：两传感器定标系数不同（PMS1: 0.1374/0.1784/0.1723/0.1894；PMS2: 0.1743/0.1784/0.1668/0.1912），脚本从 xml 自动读取，合并使用时注意反射率量级可能略有差异。
4. **跨 UTM 带**：数据覆盖 48N 和 49N 两个带（E108.0 以东进入 49N），裁剪和镶嵌时需注意统一投影。
