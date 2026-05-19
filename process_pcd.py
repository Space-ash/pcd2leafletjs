import base64
import json
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml

try:
    import open3d as o3d
except ModuleNotFoundError as exc:
    raise SystemExit("Missing dependency: open3d. Please run: pip install open3d") from exc


def resolve_path(base_dir, value):
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def load_config(config_path):
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError("config.yaml is empty or invalid.")
    return config


def load_point_cloud(input_path):
    if not input_path.exists():
        raise FileNotFoundError(f"PCD file not found: {input_path}")

    print(f"[1/7] Loading point cloud: {input_path}")
    pcd = o3d.io.read_point_cloud(str(input_path))
    count = len(pcd.points)
    if count == 0:
        raise ValueError("PCD was loaded, but it contains no points.")
    print(f"      Raw points: {count:,}")
    return pcd


def downsample_point_cloud(pcd, config):
    cfg = config.get("downsample", {})
    enabled = bool(cfg.get("enable", False))
    voxel_size = float(cfg.get("voxel_size", 0.0))

    if not enabled:
        print("[2/7] Voxel downsample disabled.")
        return pcd
    if voxel_size <= 0:
        raise ValueError("downsample.voxel_size must be greater than 0.")

    before = len(pcd.points)
    print(f"[2/7] Voxel downsample: voxel_size={voxel_size} m")
    pcd = pcd.voxel_down_sample(voxel_size=voxel_size)
    after = len(pcd.points)
    print(f"      Points: {before:,} -> {after:,}")
    return pcd


def rasterize_max_height(pcd, resolution):
    if resolution <= 0:
        raise ValueError("raster.resolution must be greater than 0.")

    points = np.asarray(pcd.points, dtype=np.float64)
    points = points[np.isfinite(points).all(axis=1)]
    if points.size == 0:
        raise ValueError("No finite XYZ points found.")

    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]

    min_x, max_x = float(x.min()), float(x.max())
    min_y, max_y = float(y.min()), float(y.max())
    min_z, max_z = float(z.min()), float(z.max())

    width = int(np.ceil((max_x - min_x) / resolution)) + 1
    height = int(np.ceil((max_y - min_y) / resolution)) + 1
    if width <= 0 or height <= 0:
        raise ValueError("Invalid raster size. Check point cloud bounds and resolution.")

    print("[3/7] Rasterizing by maximum height projection.")
    print(f"      X: {min_x:.3f} ~ {max_x:.3f} m")
    print(f"      Y: {min_y:.3f} ~ {max_y:.3f} m")
    print(f"      Z: {min_z:.3f} ~ {max_z:.3f} m")
    print(f"      Raster: {width} x {height} px, resolution={resolution} m/px")

    # Cartesian grid storage: row 0 is minY, i.e. the physical bottom.
    ix = np.floor((x - min_x) / resolution).astype(np.int64)
    iy = np.floor((y - min_y) / resolution).astype(np.int64)
    ix = np.clip(ix, 0, width - 1)
    iy = np.clip(iy, 0, height - 1)

    z_grid = np.full((height, width), -np.inf, dtype=np.float32)
    np.maximum.at(z_grid, (iy, ix), z.astype(np.float32))

    valid_mask = np.isfinite(z_grid)
    if not valid_mask.any():
        raise ValueError("Rasterization produced no valid cells.")

    z_grid[~valid_mask] = np.nan
    metadata = {
        "minX": min_x,
        "maxX": max_x,
        "minY": min_y,
        "maxY": max_y,
        "minZ": min_z,
        "maxZ": max_z,
        "resolution": resolution,
        "width": width,
        "height": height,
        "validPixelCountBeforeFill": int(valid_mask.sum()),
    }
    return z_grid, metadata


def fill_holes_with_morphology(z_grid, config):
    cfg = config.get("morphology", {})
    enabled = bool(cfg.get("enable", False))
    if not enabled:
        print("[4/7] Morphological hole filling disabled.")
        return z_grid

    try:
        from scipy import ndimage
    except ModuleNotFoundError as exc:
        raise SystemExit("morphology.enable=true requires scipy. Please run: pip install scipy") from exc

    kernel_size = int(cfg.get("kernel_size", 5))
    iterations = int(cfg.get("iterations", 3))
    fill_nearest = bool(cfg.get("fill_remaining_with_nearest", True))
    if kernel_size < 1 or kernel_size % 2 == 0:
        raise ValueError("morphology.kernel_size must be a positive odd integer.")
    if iterations < 1:
        raise ValueError("morphology.iterations must be at least 1.")

    print("[4/7] Filling raster holes with grey dilation.")
    filled = z_grid.copy()
    original_valid = np.isfinite(filled)

    for i in range(iterations):
        missing = ~np.isfinite(filled)
        if not missing.any():
            break
        work = np.where(np.isfinite(filled), filled, -np.inf)
        dilated = ndimage.grey_dilation(work, size=(kernel_size, kernel_size))
        can_fill = missing & np.isfinite(dilated)
        filled[can_fill] = dilated[can_fill]
        print(f"      Iteration {i + 1}: filled {int(can_fill.sum()):,} cells")

    remaining = ~np.isfinite(filled)
    if remaining.any() and fill_nearest:
        print(f"      Filling remaining {int(remaining.sum()):,} cells by nearest valid Z.")
        _, nearest_indices = ndimage.distance_transform_edt(
            ~np.isfinite(filled),
            return_distances=True,
            return_indices=True,
        )
        filled[remaining] = filled[tuple(nearest_indices[:, remaining])]

    after_valid = np.isfinite(filled)
    print(f"      Valid cells: {int(original_valid.sum()):,} -> {int(after_valid.sum()):,}")
    return filled.astype(np.float32)


def save_transparent_png(z_grid, output_png, colormap_name):
    print(f"[5/7] Rendering PNG: {output_png}")
    output_png.parent.mkdir(parents=True, exist_ok=True)

    valid_mask = np.isfinite(z_grid)
    valid_values = z_grid[valid_mask]
    z_min = float(valid_values.min())
    z_max = float(valid_values.max())

    normalized = np.zeros_like(z_grid, dtype=np.float32)
    if z_max > z_min:
        normalized[valid_mask] = (z_grid[valid_mask] - z_min) / (z_max - z_min)

    cmap = plt.get_cmap(colormap_name)
    rgba = cmap(np.nan_to_num(normalized, nan=0.0))
    rgba[~valid_mask, 3] = 0.0

    # z_grid row 0 is physical minY (bottom), while PNG row 0 is image top.
    # Flip vertically so pixel py=0 corresponds to physical maxY.
    rgba_for_png = np.flipud(rgba)

    height, width = z_grid.shape
    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi, frameon=False)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(rgba_for_png, origin="upper", interpolation="nearest")
    ax.set_axis_off()
    ax.set_xlim(-0.5, width - 0.5)
    ax.set_ylim(height - 0.5, -0.5)
    fig.savefig(output_png, dpi=dpi, transparent=True, pad_inches=0)
    plt.close(fig)


def downsample_z_for_frontend(z_grid, z_scale):
    if z_scale < 1:
        raise ValueError("z_export.z_scale must be at least 1.")

    # Export in PNG pixel orientation: row 0 is image top / physical maxY.
    top_down = np.flipud(z_grid).astype(np.float32)
    height, width = top_down.shape
    z_width = int(np.ceil(width / z_scale))
    z_height = int(np.ceil(height / z_scale))

    padded_h = z_height * z_scale
    padded_w = z_width * z_scale
    padded = np.full((padded_h, padded_w), np.nan, dtype=np.float32)
    padded[:height, :width] = top_down

    blocks = padded.reshape(z_height, z_scale, z_width, z_scale)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        z_small = np.nanmean(blocks, axis=(1, 3)).astype(np.float32)
    if np.isnan(z_small).any():
        nearest = np.nanmean(top_down)
        z_small = np.nan_to_num(z_small, nan=float(nearest)).astype(np.float32)
    return z_small


def write_metadata_files(metadata_json_path, metadata_js_path, metadata, z_grid, config):
    z_cfg = config.get("z_export", {})
    z_scale = int(z_cfg.get("z_scale", 4))
    decimals = int(z_cfg.get("decimals", 3))
    z_small = downsample_z_for_frontend(z_grid, z_scale)

    metadata = dict(metadata)
    metadata.update(
        {
            "zScale": z_scale,
            "zWidth": int(z_small.shape[1]),
            "zHeight": int(z_small.shape[0]),
            "zDataOrder": "row-major, top-left origin, meters",
            "validPixelCountAfterFill": int(np.isfinite(z_grid).sum()),
        }
    )

    z_precision = 10 ** decimals
    z_quantized = np.round(z_small.reshape(-1) * z_precision).astype("<i4")
    z_base64 = base64.b64encode(z_quantized.tobytes()).decode("ascii")

    js_payload = dict(metadata)
    js_payload["zData"] = z_base64
    js_payload["zEncoding"] = "base64-int32-little-endian"
    js_payload["zPrecision"] = z_precision

    metadata_json_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_js_path.parent.mkdir(parents=True, exist_ok=True)

    with metadata_json_path.open("w", encoding="utf-8") as f:
        json.dump(js_payload, f, ensure_ascii=False, indent=2)

    js_text = (
        "// Auto-generated by process_pcd.py. Do not edit by hand.\n"
        "// Leaflet renders in pixel coordinates; robot navigation uses meters.\n"
        "window.PCD_MAP_META = "
        + json.dumps(js_payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
    )
    with metadata_js_path.open("w", encoding="utf-8") as f:
        f.write(js_text)

    print(f"      Metadata JSON: {metadata_json_path}")
    print(f"      Metadata JS:   {metadata_js_path}")
    print(
        f"      Z export: {metadata['zWidth']} x {metadata['zHeight']} samples, "
        f"zScale={z_scale}, encoding=base64-int32"
    )


def collect_existing_tiles(tiles_dir):
    if not tiles_dir.exists():
        return {}

    tile_index = {}
    for png_path in tiles_dir.rglob("*.png"):
        try:
            z = png_path.parents[1].name
            x = png_path.parent.name
            y = png_path.stem
            if z.isdigit() and x.isdigit() and y.isdigit():
                tile_index.setdefault(z, []).append(f"{x}/{y}")
        except IndexError:
            continue

    for values in tile_index.values():
        values.sort(key=lambda item: tuple(int(part) for part in item.split("/")))
    return dict(sorted(tile_index.items(), key=lambda item: int(item[0])))


def update_metadata_with_tile_index(metadata_json_path, metadata_js_path, tiles_dir):
    with metadata_json_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    tile_index = collect_existing_tiles(tiles_dir)
    payload["tileIndex"] = tile_index
    payload["tileIndexFormat"] = "tileIndex[z] contains 'x/y' strings for tiles actually written by gdal2tiles"

    with metadata_json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    js_text = (
        "// Auto-generated by process_pcd.py. Do not edit by hand.\n"
        "// Leaflet renders in pixel coordinates; robot navigation uses meters.\n"
        "window.PCD_MAP_META = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
    )
    with metadata_js_path.open("w", encoding="utf-8") as f:
        f.write(js_text)

    print(f"      Tile index levels: {', '.join(tile_index.keys())}")


def run_gdal2tiles(output_png, tiles_dir, tiles_cfg):
    print("[7/7] Running gdal2tiles.")
    if tiles_dir.exists():
        shutil.rmtree(tiles_dir)
    tiles_dir.mkdir(parents=True, exist_ok=True)

    profile = str(tiles_cfg.get("profile", "raster"))
    zoom = str(tiles_cfg.get("zoom", "0-5"))
    webviewer = str(tiles_cfg.get("webviewer", "none"))

    cmd = [
        sys.executable,
        "-m",
        "osgeo_utils.gdal2tiles",
        "-p",
        profile,
        "-z",
        zoom,
        "-w",
        webviewer,
        str(output_png),
        str(tiles_dir),
    ]
    print("      " + " ".join(cmd))

    try:
        subprocess.run(cmd, check=True)
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as first_error:
        fallback = [
            "gdal2tiles.py",
            "-p",
            profile,
            "-z",
            zoom,
            "-w",
            webviewer,
            str(output_png),
            str(tiles_dir),
        ]
        print(f"      Python module call failed: {first_error}")
        print("      Trying script fallback:")
        print("      " + " ".join(fallback))
        subprocess.run(fallback, check=True)


def main():
    config_path = Path(__file__).resolve().parent / "config.yaml"
    base_dir = config_path.parent
    config = load_config(config_path)

    paths = config.get("paths", {})
    input_pcd = resolve_path(base_dir, paths.get("input_pcd", "inputs/scans.pcd"))
    output_png = resolve_path(base_dir, paths.get("output_png", "outputs/ortho_map.png"))
    tiles_dir = resolve_path(base_dir, paths.get("tiles_dir", "outputs/tiles"))
    metadata_json = resolve_path(base_dir, paths.get("metadata_json", "outputs/map_meta.json"))
    metadata_js = resolve_path(base_dir, paths.get("metadata_js", "outputs/map_meta.js"))

    resolution = float(config.get("raster", {}).get("resolution", 0.05))
    voxel_size = float(config.get("downsample", {}).get("voxel_size", 0.0))
    if voxel_size > 0 and voxel_size > resolution:
        print(
            "WARNING: downsample.voxel_size is larger than raster.resolution. "
            "The map scale remains correct, but more pixel holes may need filling."
        )

    pcd = load_point_cloud(input_pcd)
    pcd = downsample_point_cloud(pcd, config)
    z_grid, metadata = rasterize_max_height(pcd, resolution)
    z_grid = fill_holes_with_morphology(z_grid, config)
    save_transparent_png(z_grid, output_png, str(config.get("render", {}).get("colormap", "viridis")))
    print("[6/7] Writing frontend metadata and downsampled Z grid.")
    write_metadata_files(metadata_json, metadata_js, metadata, z_grid, config)
    run_gdal2tiles(output_png, tiles_dir, config.get("tiles", {}))
    update_metadata_with_tile_index(metadata_json, metadata_js, tiles_dir)

    print("Done. Open index.html in a browser.")


if __name__ == "__main__":
    main()
