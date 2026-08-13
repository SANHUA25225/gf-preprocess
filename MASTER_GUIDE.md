# 国产高分卫星 L1A 数据预处理——完整实战手册（技能 × 实战整合版）

> 整合来源：`gf-preprocess` 技能本体（SKILL.md + scripts） + 本仓库实战文档（GF-2 18 景记录、零基础讲解）
> 技术栈：**ENVI 5.6 taskengine + Python envipyengine**（非 GDAL 开源栈）

## 一、项目总览

把 GF-1/GF-2/GF-6/GF-7 的 **L1A 原始数据**（DN 值 + RPC + xml）自动处理为 **L2 级产品**：可用于定量分析的地表反射率多光谱 + 全色分辨率融合影像。

核心处理链：`辐射定标 → QUAC大气校正 → RPC+DEM正射 → NNDiffuse融合 →(可选)矢量裁剪`，全程 envipyengine 驱动 ENVI taskengine 执行，脚本化批量、断点续跑、full/geo 双模式自适应。

## 二、环境与工具链

| 组件 | 版本/路径 |
|---|---|
| ENVI | 5.6.0.0 + IDL 8.8（5.5+ 可用） |
| Python | 3.11（envipyengine 1.0.9 实测）；`C:\Users\sanhua\AppData\Local\Programs\Python\Python311\python.exe` |
| 引擎配置 | `%LOCALAPPDATA%\envipyengine\settings.cfg` → `taskengine.exe` |
| DEM | ENVI 自带 `GMTED2010.jp2`（30 弧秒全球 DEM） |

```python
import envipyengine
envipyengine.config.set("engine", r"D:\ENVI 5.6(64bit)\ENVI56\IDL88\bin\bin.x86_64\taskengine.exe")
```

## 三、适用数据源识别

| 卫星 | 多光谱命名 | 全色命名 | 像元(MUX/PAN) | 定标字段 | 状态 |
|---|---|---|---|---|---|
| GF-1 PMS | `-MSS.tiff` | `-PAN.tiff` | 8m/2m | `AbsCeof` | ✅ 实测 |
| GF-2 PMS | `-MSS1/-MSS2.tiff` | `-PAN1/-PAN2.tiff` | 3.24m/0.81m | `AbsCeof` | ✅ 实测 **18/18 景成功** |
| GF-6 PMS | `-MUX.tiff` | `-PAN.tiff` | 8m/2m | `AGain` | ✅ 实测 |
| GF-6 WFV | 3 片 CCD（`-1/-2/-3.tiff`） | 无 | 16m | `AGain` | ⚠️ 8 波段需拼接，暂不支持 |
| GF-7 DLC | `-BWDMUX.tiff` | `-BWDPAN.tiff` | 3.2m/0.8m | `AGain` | ✅ 实测 |
| GF-3 (SAR) | — | — | — | — | ❌ 跳过，提示为雷达数据 |

## 四、为什么需要这 5+1 步（零基础视角）

原始卫星数据有三个"毛病"，每一步解决一个：

| 毛病 | 解释 | 对应步骤 |
|---|---|---|
| 像素值是"数字"不是物理量 | DN 值只是传感器电压读数，不同卫星不可比 | ① 辐射定标（→ 辐亮度） |
| 大气"污染"了信号 | 像隔雾霾看风景，反射率才是地面真实值 | ② QUAC 大气校正 |
| 影像是"歪的" | 卫星侧视 + 地形起伏导致几何变形 | ③ RPC+DEM 正射 |
| 颜色和清晰度不可兼得 | 多光谱有色但模糊，全色清晰但黑白 | ④ NNDiffuse 融合 |
| 影像太大、关注区太小 | 全幅 9GB，保护区只占 1–2GB | ⑤ 矢量裁剪 |

> 中间插一步 **①′ 补波长**，是 ENVI 定标工具丢波长信息的 bug 修复（见下）。

## 五、标准处理工作流详解（5 步 + ①′）

```
原始 tiff+rpb+xml ──①ApplyGainOffset──> ①′补波长(.hdr) ──②QUAC──>
③RPC+DEM正射(UTM, NN/Bilinear) ──④NNDiffuse融合 ──⑤(可选)矢量裁剪
```

### ① 辐射定标
- **任务**：`ApplyGainOffset`；参数 `GAIN`=xml 的 `AGain`/`AbsCeof`（逗号分隔每波段）、`OFFSET`=[0,…]
- **产出**：Float64 辐亮度（GF-2 约 1.6GB/景）
- **要点**：ENVI 5.6 不认识国产卫星增益，必须从 xml 显式读入；GF-1/GF-2 用 `AbsCeof`，GF-6/GF-7 用 `AGain`

### ①′ 补波长
- **原因**：定标输出丢波长 → QUAC 报"没有有效波长信息"
- **为什么补 / 什么数据要补**：详见 [补波长详解.md](补波长详解.md)（含判断标准、幂等脚本实现）
- **做法**：`.hdr` 末尾追加两行（PMS/DLC 通用中心波长）：
  ```
  wavelength = {485.000000, 555.000000, 660.000000, 830.000000}
  wavelength units = Nanometers
  ```

### ② QUAC 大气校正
- **任务**：`QUAC`，**不传 SENSOR（默认 Generic）**；4 波段多光谱适用
- ⚠️ **勿用 "Highly Vegetated Scenes"**：需 ≥50 波段（高光谱专用）
- **产出**：Float32 地表反射率（0–1 量级），约 0.4GB/景

### ③ RPC 正射
- **任务**：`RPCOrthorectification`；DEM=GMTED2010；坐标系 `{"factory":"CoordSys","coord_sys_code":32600+带号}`（WGS84 UTM）；`OUTPUT_PIXEL_SIZE`
- **RPC**：ENVI 自动从同目录 `.rpb`/`.aux.xml` 读取，无需额外步骤
- **MUX**：**Nearest Neighbor**（保持光谱，供分类）；**PAN**：**Bilinear**（速度优先）、**原始 DN 不定标**（省 17–34GB Float64，融合实测正常）
- **UTM 带号**：优先 xml `ZoneNo`；缺失按 `int((lon+180)/6)+1` 计算；老数据再降级角点平均/文件名解析（见第六节）

### ④ NNDiffuse 融合
- **任务**：`NNDiffusePanSharpening`；低分=MUX 正射（0–1 反射率 Float）+ 高分=PAN 正射（0–4095 原始 DN UInt16）
- **产出**：与 PAN 同分辨率 4 波段 UInt16（保留波长）；混合输入由算法内部归一化，实测正常

### ⑤ 矢量裁剪（可选）
- `ReprojectVector`（参数 **`COORD_SYS`**）→ `SubsetRasterByVectorsDuBatch`
- ⚠️ 矢量必须重投影到影像坐标系，否则报"SUB_RECT 超出栅格范围"
- ⚠️ 两个隐蔽坑：`OUTPUT_VECTOR_URI` **只接受字符串路径**（不能包 URLVector 对象）；`ReprojectVector` stdout 非标准 JSON 会抛 JSONDecodeError，**但产物已生成**——必须 try/except 容错

## 六、GF-2 实战案例（18/18 景成功）

> 2026-08-12 · 广西金花茶保护区 · 2014–2026 年 · PMS1×12 + PMS2×6 · 每景 1–1.4GB tar.gz（详见 `GF2_PROCESSING_GUIDE.md`）

### 6.1 执行流程（含方法论）

1. **解压**：18 个 tar.gz → 18 个子目录（35GB），每景含 MSS+PAN tiff、xml、rpb
2. **建脚本**：参照 `gf1_pipeline.py`（AbsCeof 定标逻辑）+ `gf7_batch.py`（批量/PAN 原始 DN/full-geo 自适应）新建 `gf2_batch.py`，配置 `MUX_GSD=3.2`、`PAN_GSD=0.8`（原始 3.24/0.81 取整）
3. **先试点 1 景**（`--limit 1`）再批量——避免系统性错误浪费数小时
4. **批量第 1 批**：8 成功 + 8 失败 → **诊断修复后重跑** → 9 新增成功（自动跳过已有 9 景，断点续跑）
5. **验证**：`inspect_image.py` 抽检（像元/投影/波长/处理阶段标签）

### 6.2 单景耗时与产物（full 模式）

| 步骤 | 耗时 | 产物 | 大小 |
|---|---|---|---|
| ① 定标 | 9s | mux_cal.dat (Float64) | 1.62GB |
| ② QUAC | 95s | mux_quac.dat (Float32) | 0.40GB |
| ③ MUX 正射 | 23s | mux_ortho.dat (3.2m, NN) | 0.55GB |
| ④ PAN 正射 | 201s | pan_ortho.dat (0.8m, Bilinear, 原始DN) | 2.21GB |
| ⑤ 融合 | 289s | pansharp.dat (0.8m 4波段) | 8.82GB |
| ⑥ 裁剪 | 17s | clip/pansharp_boundary_utm.dat | **1.06GB**（缩减 88%） |

**单景约 10 分钟（full）/ 7 分钟（geo），18 景总计约 3 小时。**

### 6.3 实战遇到的问题与修复

| # | 问题 | 根因 | 解决 |
|---|---|---|---|
| 1 | 裁剪报 `OUTPUT_VECTOR_URI failed hydration` | `ReprojectVector` 该参数只接受字符串路径 | 直接传字符串，不包 URLVector 对象 |
| 2 | 老数据崩溃 `float() ... not 'NoneType'` | 老 xml 无 `CenterLongitude`（只有角点） | **UTM 带号 4 级降级**（见下） |
| 3 | 老数据无定标系数 | xml 缺 `AbsCeof` | 自动降级 **geo 模式**（仅正射+融合，输出原始 DN） |
| 4 | D 盘仅 120G 可用 | 全幅融合 ~9GB/景，18 景峰值 >200G | 裁剪后删全幅 + 逐景清理中间产物 |
| 5 | GF-2 命名与现役卫星均不同 | `-MSS1/-MSS2` 而非 `-MUX/-MSS/-BWDMUX` | 通配符 `*MSS*.tiff` 匹配 |

**UTM 带号 4 级降级策略**（可复用到所有卫星）：
```
ZoneNo → CenterLongitude → 四角点经度取平均 → 文件名 E(\d+) 解析 → 报错
```

### 6.4 成果统计

- 成功 **18/18**（full 9 景 2023+ 有 AbsCeof；geo 9 景 2014–2022 缺系数）
- 产品：0.8m 裁剪融合（UInt16，带波长）+ 3.2m 多光谱正射
- 验证：0.80m 像元 ✅ 4 波段 ✅ UTM 48N/49N ✅ NNDiffuse 标签 ✅ 波长 485–830nm ✅

## 七、关键参数速查

| 参数 | GF-1 PMS | GF-6 PMS | GF-7 DLC | 来源 |
|---|---|---|---|---|
| 定标字段 | `AbsCeof` | `AGain` | `AGain` | 多光谱 `.xml` |
| 中心波长(nm) | 485/555/660/830 | 同左 | 同左 | PMS/DLC 官方 |
| UTM 带号 | 依经度 | `ZoneNo` 优先 | 依经度（ZoneNo 常空） | xml 或公式 |
| MUX 正射像元 | 8m | 8m | 3.2m | xml `ImageGSD` |
| PAN 正射像元 | 2m | 2m | 0.8m | xml `ImageGSD` |

**ENVI 传参格式**：Raster `{"factory":"URLRaster","url":路径}`；Vector `{"factory":"URLVector","url":路径}`；坐标系 `{"factory":"CoordSys","coord_sys_code":EPSG}`（UTM=32600+带号）；⚠️ 不支持 `file:///` 协议。

## 八、重采样选择与分类用途

| 方法 | 适用 | 实战配置 |
|---|---|---|
| **Nearest Neighbor** | 像元级光谱分类（保持原始光谱） | MUX 正射 |
| **Bilinear** | 全色（速度+边缘好） | PAN 正射（21.5 亿像素，Cubic 过慢） |
| Cubic Convolution | 定量分析平滑 | GF-1 早期用过，已弃 |

**分类用途分流（重要）**：
- **像元级光谱分类**（SVM/随机森林/监督分类）→ 用**未融合**的 8m/3.2m 正射多光谱（QUAC 反射率），**不用融合影像**（NNDiffuse 已改变光谱值）
- **面向对象分类** → 用 2m/0.8m 融合影像（分割+纹理+光谱）
- **辅助特征** → NDVI、GLCM 纹理（从融合影像算）、DEM 坡度坡向

## 九、磁盘与性能优化

1. **PAN 原始 DN 正射**：峰值磁盘减半（省 17–34GB Float64/景）
2. **中间产物一步一删**：QUAC 后删定标、正射后删 QUAC、融合后删 PAN 正射、裁剪后删全幅 pansharp
3. **输出选空间足的盘**：融合结果 10–36GB/景
4. **Float64 定标产物大**（4.3G/景）→ QUAC 后转 Float32/UInt16 显著减小

## 十、ENVI 5.6 任务命名差异

- **原生任务**（大写参数）：`ApplyGainOffset`、`QUAC`、`RPCOrthorectification`、`NNDiffusePanSharpening`、`ReprojectVector`
- **DuBatch 版**（小写参数、output_dir 模式）：`BandMathDuBatch`、`SubsetRasterByVectorsDuBatch`
- ⚠️ `BandMath`、`SubsetRasterByVectors` **不存在**，勿用

## 十一、踩坑速查（12 条）

1. `BandMathDuBatch` 不支持多波段数组表达式，变量小写 `b1`；定标用 `ApplyGainOffset` 一步到位
2. 定标丢波长 → hdr 补 2 行，否则 QUAC 报"没有有效波长信息"
3. QUAC "Highly Vegetated Scenes" 需 ≥50 波段，4 波段用默认 Generic
4. RPC 自动读 `.rpb`/`.aux.xml`，正射无需额外步骤
5. 裁剪前必须 `ReprojectVector`（参数 `COORD_SYS`），否则报 SUB_RECT 超范围
6. `ReprojectVector` stdout 非 JSON → try/except 容错 + 检查产物
7. `ReprojectVector` 的 `OUTPUT_VECTOR_URI` **只接受字符串路径**
8. GF-6 `-MUX`/`AGain` vs GF-1 `-MSS`/`AbsCeof`；xml 优先 `ZoneNo`
9. GF-7 前视 `-FWDPAN` 不参与融合，只处理 `-BWDPAN`+`-BWDMUX`
10. 老数据（2022–2024）缺定标系数 → 自动降级 geo 模式；`AGain`="NULL" 字符串也视为无效
11. 老数据缺 `CenterLongitude` → UTM 带号 4 级降级
12. 处理/裁剪前检查空间重叠（曾遇影像与保护区相距 23km）

## 十二、Agent 操作模式（执行规范）

1. **探测**：`ls` 目录 → `grep` xml（AGain/AbsCeof、ZoneNo、中心经纬度、像元尺寸）→ 定卫星与模式
2. **确认**：有系数 → full（定标+QUAC）；无 → geo（仅正射+融合）；分类用途提示用 NN
3. **批量**：串行执行，先 `--limit 1` 试点，每步验证文件大小；加"跳过已完成"断点续跑
4. **磁盘**：处理前检查剩余空间；PAN 原始 DN（默认策略）；紧张时一步一删
5. **交付**：打印产物清单（pansharp.dat + mux_ortho.dat + hdr），`inspect_image.py` 抽检
6. **关机**：仅用户明确要求时 `--shutdown` 触发 `shutdown /s /t 60`

## 十三、脚本清单（6 个）

| 脚本 | 用途 | 使用方式 |
|---|---|---|
| `gf1_pipeline.py` | GF-1 单景 + 矢量裁剪 | `python gf1_pipeline.py <影像目录> <shp> <输出目录>` |
| `gf2_batch.py` | GF-2 批量（PMS1/PMS2，full/geo 自适应，6 步） | `python gf2_batch.py [--limit N] [--skip N] [--shutdown]` |
| `gf2_fix_clip.py` | 对已有融合结果补做裁剪 | 修复 OUTPUT_VECTOR_URI 后运行 |
| `gf6_batch.py` | GF-6 PMS 批量（平铺目录） | `python gf6_batch.py [--shutdown]` |
| `gf7_batch.py` | GF-7 DLC 批量（子目录） | `python gf7_batch.py [场景名...] [--shutdown]` |
| `inspect_image.py` | 处理阶段判断（尺寸/像元/投影/波长/阶段） | `python inspect_image.py <file.dat>` |

## 十四、异常处理约定

| 异常 | 处理 |
|---|---|
| 缺定标系数 | GEO 模式，log 记录，继续 |
| 单景失败 | try/except 捕获，记录，继续下一景 |
| RPC 缺失 | 查 `.rpb`/`.rpb.aux.xml`，全缺则跳过该景 |
| 融合失败 | 查 PAN/MUX 正射大小与坐标系（均应 UTM）；极少情况改 PAN 定标重跑 |
| 磁盘不足 | 换输出盘或提示清理 |

## 十五、遗留与建议

- **GF-6 WFV**（8 波段 3 片 CCD）需拼接处理，暂未支持
- geo 模式产品无波长：可从同区域 full 景 `.hdr` 复制 wavelength 字段补上
- PMS1/PMS2 定标系数不同（PMS1: 0.1374/0.1784/0.1723/0.1894；PMS2: 0.1743/0.1784/0.1668/0.1912），脚本自动读 xml，合并使用时注意反射率量级差异
- 跨 UTM 带（48N/49N）镶嵌需统一投影
- 产品确认无误后删解压目录（35GB）回收空间
