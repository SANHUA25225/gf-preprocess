# GF Satellite Preprocessor

面向**国产高分系列（GF-1 / GF-6 / GF-7）L1A 级数据**的自动化预处理工具集。基于 ENVI 5.6+ 的 taskengine 引擎，通过 Python envipyengine 驱动，实现辐射定标、大气校正、RPC 正射、融合、矢量裁剪一条龙处理。

## 适用卫星

| 卫星 | 多光谱命名 | 全色命名 | 像元 (MUX/PAN) | 定标字段 | 实测状态 |
|---|---|---|---|---|---|
| GF-1 PMS | `-MSS.tiff` | `-PAN.tiff` | 8m / 2m | `AbsCeof` | ✅ 实测 |
| GF-6 PMS | `-MUX.tiff` | `-PAN.tiff` | 8m / 2m | `AGain` | ✅ 实测 |
| GF-6 WFV | 3 片 CCD | 无 | 16m | `AGain` | ⚠️ 待适配 |
| GF-7 DLC | `-BWDMUX.tiff` | `-BWDPAN.tiff` | 3.2m / 0.8m | `AGain` | ✅ 实测 |
| GF-2 | 待测 | 待测 | 3.2m / 0.8m | 待确认 | 未实测 |
| GF-3 (SAR) | — | — | — | — | ❌ 不支持 |

## 环境要求

- **ENVI 5.5+**（含 IDL 8.8+ 和 `taskengine.exe`）
- **Python 3.8–3.11**（实测 3.11 通过）
- **envipyengine**（ENVI 官方 Python 任务引擎，PyPI 安装）
- ENVI 自带的 **GMTED2010.jp2** 全球 DEM（正射校正必需）

## 快速开始

### 1. 安装 envipyengine 并配置引擎路径

```bash
pip install envipyengine
python -c "import envipyengine; envipyengine.config.set('engine', r'D:\ENVI56\IDL88\bin\bin.x86_64\taskengine.exe')"
```

### 2. 修改脚本中的配置项

每个脚本顶部有 `# ====== 配置 ======` 区，按需修改：
- `BASE`：影像所在目录
- `OUT_BASE`：输出目录
- `DEM`：ENVI 自带 GMTED2010.jp2 的路径

### 3. 运行处理

```bash
# GF-6 批量（平铺目录，自动识别 MUX+PAN 对）
python scripts/gf6_batch.py

# GF-7 批量（子目录结构，每景独立文件夹）
python scripts/gf7_batch.py

# GF-1 单景 + 矢量裁剪
python scripts/gf1_pipeline.py "影像目录" "矢量.shp" "输出目录"

# 完成后自动关机
python scripts/gf6_batch.py --shutdown
```

### 4. 质量检查

```bash
python scripts/inspect_image.py pansharp.dat
# 输出：处理阶段推断（融合产品/大气校正后/正射后/原始L1A）
```

## 处理流程（5 步）

```
原始 tiff+rpb+xml
  ├─① ApplyGainOffset 辐射定标（GAIN 从 xml 自动读）
  ├─①′ 补波长到 .hdr（定标丢波长，QUAC 必需）
  ├─② QUAC 大气校正（4 波段多光谱，Generic 模式）
  ├─③ RPC+DEM 正射到 UTM（MUX: Nearest Neighbor, PAN: Bilinear）
  ├─④ NNDiffuse 融合（2m/0.8m 4 波段）
  └─⑤ 矢量重投影 + 裁剪（可选）
```

## 输出目录结构

```
输出目录/
├── batch_log.txt                # 处理日志
├── GF6_PMS_E107.6_.../          # 每景独立子目录
│   ├── pansharp.dat             # 融合影像（最终产品）
│   ├── pansharp.hdr             # 元数据（波长、UTM投影）
│   ├── mux_ortho.dat            # 多光谱正射（分类用）
│   └── mux_ortho.hdr
└── GF6_PMS_E107.8_.../
    └── ...
```

## 脚本清单

| 脚本 | 用途 | 输入模式 |
|---|---|---|
| `gf6_batch.py` | GF-6 PMS 批处理（full/geo 自适应） | 平铺目录 |
| `gf7_batch.py` | GF-7 DLC 批处理（full/geo 自适应） | 子目录 |
| `gf1_pipeline.py` | GF-1 PMS 单景 + 裁剪 | 参数指定 |
| `inspect_image.py` | 处理阶段判断工具 | 任意影像文件 |

## 关键经验

- **老数据缺定标系数**（2022–2024）：xml 只有 `GainMode` 无 `AGain`/`AbsCeof` → 自动降级为几何模式（正射+融合，无大气校正）
- **PAN 用原始 DN 正射**：省约 17–34GB Float64 磁盘，NNDiffuse 归一化处理实测正常
- **补波长**：ApplyGainOffset 丢波长信息，QUAC 强制要求，在 `.hdr` 追加 2 行即可
- **矢量裁剪前重投影**：`ReprojectVector`（参数 `COORD_SYS`），否则 DuBatch 报错

## 许可

MIT — 详见各脚本头部注释。基于 ENVI taskengine，使用时需有效 ENVI 许可。
