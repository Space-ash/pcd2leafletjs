# pcd2leafletjs

`pcd2leafletjs` 是一个将激光雷达点云 `.pcd` 文件转换为可交互 Leaflet.js 地图的轻量级项目。项目先用 Python 将三维点云投影、栅格化并切分为地图瓦片，再由前端页面通过 Leaflet.js 在浏览器中加载瓦片，实现点云地图浏览、旋转、测距、兴趣点标注和机器人导航坐标导出。

## 项目完成的工作

本项目围绕“点云地图可视化与导航点采集”完成了以下工作：

- 将输入的 PCD 点云读取为三维坐标数据，并按最高点投影生成二维高程栅格。
- 将高程栅格渲染为带透明背景的正射 PNG 图像。
- 使用 `gdal2tiles` 将大幅 PNG 切分为多级地图瓦片，便于浏览器按需加载。
- 生成 `map_meta.json` 和 `map_meta.js`，保存地图边界、分辨率、Z 轴高度数据、瓦片索引等前端所需元数据。
- 基于 Leaflet.js 构建离线可用的像素坐标地图浏览器。
- 在浏览器中支持地图旋转、缩放、平移、鼠标位置三维坐标显示、两点距离测量、兴趣点添加与导出。
- 提供像素坐标与物理坐标之间的转换接口，方便机器人导航系统读取起点、终点和途径点。

## Leaflet.js 的作用

项目使用 Leaflet.js 作为前端地图渲染和交互框架。Leaflet 原本常用于 WebGIS 地图，本项目通过 `L.CRS.Simple` 将其改造成“图像/像素坐标系地图”：

- 使用 `L.map` 创建浏览器中的地图容器。
- 使用 `L.CRS.Simple` 让地图坐标直接对应栅格图像像素，而不是经纬度。
- 使用自定义 `L.TileLayer` 加载 `outputs/tiles/{z}/{x}/{y}.png` 瓦片。
- 使用 `L.Control` 实现旋转、缩放、清除测量、清除兴趣点、导出兴趣点等控件。
- 使用 `L.marker`、`L.popup`、`L.polyline` 实现兴趣点标记、坐标弹窗和测距线。
- 通过 `map.project`、`map.unproject` 完成 Leaflet 像素点与项目物理坐标之间的转换。

因此，本项目不是简单展示一张图片，而是将点云生成的栅格地图封装成可缩放、可旋转、可交互、可导出导航点的 Leaflet 离线地图应用。

## 代码管线流程

项目的数据处理和展示流程如下：

```text
inputs/scans.pcd
    |
    v
process_pcd.py
    |
    +-- 读取 PCD 点云
    +-- 可选体素降采样
    +-- 按 X/Y 分辨率栅格化
    +-- 每个栅格保留最大 Z 值
    +-- 可选形态学补洞
    +-- 渲染透明 PNG 正射图
    +-- 导出前端元数据和降采样 Z 网格
    +-- 调用 gdal2tiles 生成瓦片
    +-- 写入瓦片索引
    |
    v
outputs/ortho_map.png
outputs/tiles/
outputs/map_meta.json
outputs/map_meta.js
    |
    v
index.html + dist/leaflet.js
    |
    v
浏览器中的交互式 Leaflet 点云地图
```

### 后端处理流程

`process_pcd.py` 是点云转换管线的核心脚本，主要步骤为：

1. 读取 `config.yaml`，解析输入、输出、分辨率、渲染方式和切片参数。
2. 使用 Open3D 读取 `inputs/scans.pcd`。
3. 根据配置可选执行体素降采样，以降低处理成本。
4. 将点云按 `raster.resolution` 投影到二维栅格，X/Y 表示平面位置，Z 表示高度。
5. 同一栅格内存在多个点时保留最大 Z 值，生成最大高度投影图。
6. 根据 `morphology` 配置对空洞执行灰度膨胀或最近邻填充。
7. 使用 Matplotlib 将高度值映射为颜色，输出透明背景的 `outputs/ortho_map.png`。
8. 将地图范围、分辨率、Z 轴采样数据等写入 `outputs/map_meta.json` 和 `outputs/map_meta.js`。
9. 调用 GDAL 的 `gdal2tiles` 将 PNG 切成多级瓦片。
10. 扫描实际生成的瓦片文件，向元数据中写入 `tileIndex`，避免前端请求不存在的瓦片。

### 前端展示流程

`index.html` 是无需构建的静态前端页面，主要流程为：

1. 加载本地 `dist/leaflet.css` 和 `dist/leaflet.js`。
2. 加载自动生成的 `outputs/map_meta.js`，读取 `window.PCD_MAP_META`。
3. 根据元数据创建 `L.CRS.Simple` 像素坐标地图。
4. 通过自定义瓦片图层加载 `outputs/tiles` 中的 GDAL 瓦片。
5. 安装旋转、缩放、测距、兴趣点和鼠标状态控件。
6. 解码前端 Z 轴数据，用于坐标查询和三维距离计算。
7. 将兴趣点保存到 `localStorage`，并支持导出为 JSON。

## 功能特性

- PCD 点云文件读取与基础校验。
- 可选体素降采样。
- 点云到二维高程栅格的最大高度投影。
- 按米/像素设置地图分辨率。
- 栅格空洞的形态学填充。
- 高程图颜色映射渲染，默认使用 `viridis`。
- 大图切片，支持多级缩放。
- Leaflet 离线静态地图展示，无需前端构建工具。
- 鼠标悬停实时显示物理坐标 `X/Y/Z`。
- 左键拖动平移地图。
- 右键拖动旋转地图。
- 控件输入或按钮微调地图旋转角度。
- 地图缩放倍数显示与缩放按钮。
- 单击两点测量水平距离、高差和三维距离。
- 双击添加兴趣点，可选择起点、终点或途径点。
- 兴趣点选中、删除、持久化和 JSON 导出。
- 暴露机器人导航相关的全局接口：
  - `window.pixelToPhysical(...)`
  - `window.physicalToPixel(...)`
  - `window.physicalToPixelPoint(...)`
  - `window.ROBOT_NAV_POIS`
  - `window.getRobotNavPoisJson()`

## 目录结构

```text
pcd2leafletjs/
├── config.yaml              # 全局配置文件
├── process_pcd.py           # PCD 到 Leaflet 瓦片地图的处理脚本
├── index.html               # Leaflet 静态地图页面
├── dist/                    # Leaflet.js 本地静态资源
├── inputs/
│   └── scans.pcd            # 默认输入点云文件
└── outputs/
    ├── ortho_map.png        # 点云渲染后的正射 PNG
    ├── map_meta.json        # 前端元数据 JSON
    ├── map_meta.js          # 前端直接加载的元数据脚本
    └── tiles/               # gdal2tiles 生成的地图瓦片
```

## 环境要求

- Python 3.9 或更高版本
- 支持现代 JavaScript 的浏览器
- Python 依赖：
  - `open3d`
  - `numpy`
  - `matplotlib`
  - `pyyaml`
  - `scipy`
  - `gdal`

`scipy` 用于形态学补洞。当 `config.yaml` 中 `morphology.enable` 为 `false` 时可以不使用该功能，但建议完整安装。

## 安装依赖

推荐使用 Conda 安装 GDAL，再使用 `requirement.txt` 安装 Python 依赖：

```bash
conda create -n pcd2leafletjs python=3.10
conda activate pcd2leafletjs
conda install -c conda-forge gdal
python -m pip install -r requirement.txt
```

如果系统已经正确安装 GDAL，也可以直接使用 pip 安装 Python 包：

```bash
python -m pip install -r requirement.txt
```

## 配置说明

主要配置位于 `config.yaml`：

```yaml
paths:
  input_pcd: inputs/scans.pcd
  output_png: outputs/ortho_map.png
  tiles_dir: outputs/tiles
  metadata_json: outputs/map_meta.json
  metadata_js: outputs/map_meta.js
```

常用参数说明：

| 配置项 | 说明 |
| --- | --- |
| `paths.input_pcd` | 输入 PCD 点云文件路径 |
| `paths.output_png` | 中间正射 PNG 输出路径 |
| `paths.tiles_dir` | Leaflet 瓦片输出目录 |
| `paths.metadata_json` | 元数据 JSON 输出路径 |
| `paths.metadata_js` | 前端加载的元数据 JS 输出路径 |
| `downsample.enable` | 是否启用体素降采样 |
| `downsample.voxel_size` | 体素大小，单位通常为米 |
| `raster.resolution` | 栅格分辨率，单位为米/像素 |
| `morphology.enable` | 是否启用栅格补洞 |
| `morphology.kernel_size` | 形态学核大小，必须为正奇数 |
| `morphology.iterations` | 补洞迭代次数 |
| `z_export.z_scale` | 前端 Z 轴网格相对 PNG 的降采样倍率 |
| `z_export.decimals` | Z 值导出小数位数 |
| `render.colormap` | Matplotlib 颜色映射名称 |
| `tiles.profile` | `gdal2tiles` 切片 profile，默认 `raster` |
| `tiles.zoom` | 切片缩放层级，例如 `0-5` |
| `tiles.webviewer` | `gdal2tiles` 是否生成额外 Web Viewer，默认 `none` |

## 使用方法

### 1. 准备点云数据

将待转换的 PCD 文件放入 `inputs/` 目录，并在 `config.yaml` 中设置路径。例如默认路径为：

```yaml
paths:
  input_pcd: inputs/scans.pcd
```

### 2. 修改转换参数

根据点云范围和需要的地图精度调整分辨率：

```yaml
raster:
  resolution: 0.01
```

`resolution` 越小，地图越精细，但 PNG 和瓦片数量会增大，处理时间也会增加。

### 3. 运行转换脚本

在项目根目录运行：

```bash
python process_pcd.py
```

运行完成后会生成：

```text
outputs/ortho_map.png
outputs/map_meta.json
outputs/map_meta.js
outputs/tiles/
```

### 4. 打开地图页面

直接用浏览器打开项目根目录下的 `index.html`：

```text
index.html
```

页面会自动加载 `dist/leaflet.js`、`outputs/map_meta.js` 和 `outputs/tiles/`。

如果浏览器限制本地文件加载，可以在项目根目录启动一个静态服务器：

```bash
python -m http.server 8000
```

然后访问：

```text
http://localhost:8000/index.html
```

## 前端交互说明

- 左键拖动：平移地图。
- 右键拖动：围绕当前地图中心旋转地图。
- 左上角旋转控件：输入角度或点击 `+` / `-` 微调旋转。
- 左上角缩放控件：查看缩放倍数并进行缩放。
- 鼠标移动：左下角显示当前 `X/Y/Z` 物理坐标。
- 单击地图：放置测量点；连续选择两个点后显示水平距离、高差和三维距离。
- 双击地图：添加兴趣点，可选择起点、终点或途径点。
- 清除测量：删除当前测距点和测距线。
- 清除兴趣点：删除当前选中的兴趣点。
- 导出兴趣点：下载 `robot_nav_pois.json`。

## 导出的兴趣点格式

导出的 JSON 包含点类型、像素坐标和物理坐标：

```json
[
  {
    "id": "poi-1",
    "type": "start",
    "typeLabel": "起点",
    "pixel": {
      "px": 123.456,
      "py": 789.012
    },
    "physical": {
      "x": 1.2345,
      "y": 6.789,
      "z": 0.1234
    }
  }
]
```

其中：

- `type` 可为 `start`、`goal`、`waypoint`。
- `pixel.px` / `pixel.py` 是地图像素坐标。
- `physical.x` / `physical.y` / `physical.z` 是点云物理坐标，通常单位为米。

## 输出文件说明

| 文件或目录 | 说明 |
| --- | --- |
| `outputs/ortho_map.png` | 点云投影后的正射高程图 |
| `outputs/tiles/` | Leaflet 加载的瓦片目录 |
| `outputs/map_meta.json` | 可读性更好的元数据文件 |
| `outputs/map_meta.js` | 前端页面直接加载的元数据脚本 |

## 注意事项

- `outputs/map_meta.js` 由 `process_pcd.py` 自动生成，不建议手动修改。
- 当前前端页面依赖 `outputs/map_meta.js` 和 `outputs/tiles/`，首次打开前需要先运行转换脚本。
- 如果调整了 `config.yaml`、替换了 PCD 文件或修改了分辨率，需要重新运行 `python process_pcd.py`。
- 点云坐标单位默认按米理解，`raster.resolution` 也应使用相同单位。
- 大规模点云会生成较大的 PNG、元数据和瓦片文件，必要时可开启体素降采样或调大 `raster.resolution`。

## 许可证

项目中包含 Leaflet.js 分发文件。Leaflet.js 是开源项目，使用时请遵守其原始许可证。当前仓库如需发布，建议补充本项目自身的许可证文件。
