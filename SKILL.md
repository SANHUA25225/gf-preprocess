# 高分系列遥感数据预处理（ENVI 实战版）

## 角色与目标
国产高分卫星（GF-1/GF-6/GF-7）L1A 数据的自动化预处理代理。使用 **ENVI taskengine + Python envipyengine**，输出 L2 级产品（地表反射率、正射融合影像）。

## 适用数据源识别
| 卫星 | 命名 | 像元尺寸 | 定标字段 |
|---|---|---|---|
| GF-1 PMS | `-MSS.tiff` + `-PAN.tiff` | 8m/2m | `AbsCeof` |
| GF-6 PMS | `-MUX.tiff` + `-PAN.tiff` | 8m/2m | `AGain` |
| GF-6 WFV | 分片 CCD | 16m | `AGain` |
| GF-7 DLC | `-BWDMUX.tiff` + `-BWDPAN.tiff`（前视 `-FWDPAN` 不参与融合） | 3.2m/0.8m | `AGain` |
| GF-3 SAR | — | — | 跳过，提示为雷达数据 |

## 标准处理工作流（5 步）

```
tiff+rpb+xml ──①ApplyGainOffset──> ①′补波长(.hdr) ──②QUAC──> ③RPC+DEM正射(UTM,NN/Bilinear) ──④NNDiffuse融合 ──⑤(可选)矢量裁剪
```

### ① 辐射定标
- 任务 `ApplyGainOffset`，从 xml `AGain`/`AbsCeof` 读增益
- ⚠️ 老数据（2022–2024）可能缺定标系数 → 降级几何模式

### ①′ 补波长
- 定标输出丢波长 → QUAC 报错
- 在 `.hdr` 追加：`wavelength = {485, 555, 660, 830}` nm

### ② QUAC 大气校正
- 4 波段多光谱用默认 Generic；⚠️ 勿用 "Highly Vegetated Scenes"（需 ≥50 波段）

### ③ RPC 正射
- DEM = ENVI 自带 GMTED2010.jp2；RPC 自动从 `.rpb` 读取
- MUX: Nearest Neighbor（分类用保持光谱）；PAN: Bilinear（速度优先）

### ④ NNDiffuse 融合
- PAN 用原始 DN 正射（不定标），省 17–34GB Float64

### ⑤ 矢量裁剪（可选）
- `ReprojectVector` → `SubsetRasterByVectorsDuBatch`
- 矢量必须重投影到影像坐标系（参数 `COORD_SYS`），否则报 SUB_RECT 超范围

## 关键参数速查
- **ENVIRaster 传参**：`{"factory":"URLRaster","url":"本地路径"}`
- **ENVIVector**：`{"factory":"URLVector","url":"path.shp"}`
- **ENVICoordSys**：`{"factory":"CoordSys","coord_sys_code":EPSG码}`
- 不支持 `file:///` 协议
- UTM 带号：优先 xml `ZoneNo`，否则 `int((lon+180)/6)+1`

## 踩坑速查
1. `BandMathDuBatch` 不支持数组表达式，变量小写 `b1`；定标用 `ApplyGainOffset`
2. 定标输出丢波长 → hdr 补 2 行
3. "Highly Vegetated Scenes" ≥50 波段，4 波段用 Generic
4. RPC 自动读取 `.rpb`/`.aux.xml`
5. 裁剪前 `ReprojectVector`（参数 `COORD_SYS`）
6. `ReprojectVector` stdout 非 JSON → try/except 容错
7. GF-6 命名 `-MUX`，GF-1 命名 `-MSS`
8. GF-7 DLC 前视 `-FWDPAN` 跳过
9. 老数据缺 `AGain`/`AbsCeof` → 降级 GEO 模式
10. 处理前检查影像与矢量空间重叠

## 操作模式
1. **探测**：ls + grep xml → 确认卫星/系数/带号
2. **分派**：有系数 → full；无 → geo；分类用途 → MUX 正射用 NN
3. **批量**：串行处理，每步验证文件大小，磁盘告警则切换输出盘
4. **交付**：检查 hdr（description/波段/UTM）+ inspect_image.py 抽检
5. **关机**：仅 `--shutdown` 参数触发
