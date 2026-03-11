import argparse
import io
import json
import math
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch
from PIL import Image, ImageDraw


plt.switch_backend("Agg")

PROJECT_TITLE = (
    "OpenStreetMap History Audit: Shajareh Tayyebeh Girls' School and Adjacent "
    "Military Mapping in Minab, Iran"
)
SHORT_CONTEXT = (
    "This repository is a post-incident OpenStreetMap history review focused on the "
    "mapping state around the strike on the Shajareh Tayyebeh girls' school in Minab "
    "on 28 February 2026. The outputs are intended for forensic, journalistic, and "
    "technical review, not operational analysis."
)
STRIKE_DATE_DEFAULT = "2026-02-28"
OUTPUT_DIR_DEFAULT = Path("results")

WAY_METADATA = {
    1485767423: {
        "label": "Suspected IRGC compound",
        "short_label": "Military base",
        "role": "compound",
        "color": "#00BCD4",
    },
    1484791929: {
        "label": "Shajareh Tayyebeh girls' school",
        "short_label": "School",
        "role": "school",
        "color": "#1976D2",
    },
    942760673: {
        "label": "Sayyid al-Shuhada-Asif barracks area",
        "short_label": "Barracks",
        "role": "barracks_area",
        "color": "#FFA726",
    },
}
WAY_IDS = list(WAY_METADATA)
MILESTONE_ORDER = ["first_version", "last_pre_strike", "first_post_strike", "latest"]
MILESTONE_LABELS = {
    "first_version": "First mapped",
    "last_pre_strike": "Last pre-strike",
    "first_post_strike": "First post-strike",
    "latest": "Latest",
}
MILESTONE_STYLES = {
    "first_version": {"color": "#6C757D", "linestyle": "--", "linewidth": 2.0},
    "last_pre_strike": {"color": "#1565C0", "linestyle": "-", "linewidth": 2.6},
    "first_post_strike": {"color": "#EF6C00", "linestyle": "-.", "linewidth": 2.2},
    "latest": {"color": "#C62828", "linestyle": "-", "linewidth": 3.0},
}
EDIT_TYPE_STYLES = {
    "creation": {"marker": "o", "color": "#2E7D32"},
    "geometry + tag change": {"marker": "D", "color": "#8E24AA"},
    "geometry change": {"marker": "s", "color": "#FB8C00"},
    "tag change": {"marker": "^", "color": "#1565C0"},
    "visibility change": {"marker": "X", "color": "#C62828"},
    "no structural change": {"marker": "o", "color": "#6D4C41"},
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "IranGirlsSchoolHistoryAudit/2.0"})
TILE_CACHE = {}


def parse_args():
    parser = argparse.ArgumentParser(description="Generate strike-date-aware OSM audit outputs.")
    parser.add_argument("--strike-date", default=STRIKE_DATE_DEFAULT)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR_DEFAULT))
    parser.add_argument("--skip-animation", action="store_true")
    return parser.parse_args()


def way_meta(way_id):
    return WAY_METADATA[way_id]


def way_label(way_id):
    return way_meta(way_id)["label"]


def way_short_label(way_id):
    return way_meta(way_id)["short_label"]


def way_color_hex(way_id):
    return way_meta(way_id)["color"]


def way_display_label(way_id):
    return f"{way_short_label(way_id)} (Way {way_id})"


def way_annotation_label(way_id):
    return f"{way_short_label(way_id)}\nWay {way_id}"


def parse_timestamp(value):
    return pd.to_datetime(value, utc=True, errors="coerce")


def format_timestamp(value):
    if value is None or pd.isna(value):
        return "n/a"
    return pd.Timestamp(value).tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S UTC")


def iso_timestamp(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).tz_convert("UTC").isoformat()


def safe_float(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def compact_text(value, limit=140):
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def hex_to_rgb(color_hex):
    value = color_hex.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def close_coords(coords):
    if len(coords) < 2 or coords[0] == coords[-1]:
        return coords[:]
    return coords + [coords[0]]


def geometry_centroid(coords):
    points = coords[:-1] if len(coords) >= 2 and coords[0] == coords[-1] else coords
    if not points:
        return None
    lat = sum(point[0] for point in points) / len(points)
    lon = sum(point[1] for point in points) / len(points)
    return lat, lon


def latlon_to_local_xy(coords, reference=None):
    if not coords:
        return []

    if reference is None:
        lat0_deg = sum(lat for lat, _ in coords) / len(coords)
        lon0_deg = sum(lon for _, lon in coords) / len(coords)
    else:
        lat0_deg, lon0_deg = reference

    lat0 = math.radians(lat0_deg)
    lon0 = math.radians(lon0_deg)
    radius_m = 6_371_000.0

    xy = []
    for lat, lon in coords:
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        x = (lon_rad - lon0) * math.cos(lat0) * radius_m
        y = (lat_rad - math.radians(lat0_deg)) * radius_m
        xy.append((x, y))
    return xy


def polyline_length_m(coords):
    if len(coords) < 2:
        return 0.0
    xy = latlon_to_local_xy(coords)
    total = 0.0
    for index in range(len(xy) - 1):
        x1, y1 = xy[index]
        x2, y2 = xy[index + 1]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def polygon_perimeter_m(coords):
    if len(coords) < 3:
        return polyline_length_m(coords)
    return polyline_length_m(close_coords(coords))


def polygon_area_sq_m(coords):
    if len(coords) < 3:
        return 0.0
    closed = close_coords(coords)
    xy = latlon_to_local_xy(closed)
    area = 0.0
    for index in range(len(xy) - 1):
        x1, y1 = xy[index]
        x2, y2 = xy[index + 1]
        area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2.0


def minimum_vertex_distance_m(coords_a, coords_b):
    if not coords_a or not coords_b:
        return None
    all_points = coords_a + coords_b
    reference = (
        sum(point[0] for point in all_points) / len(all_points),
        sum(point[1] for point in all_points) / len(all_points),
    )
    xy_a = latlon_to_local_xy(coords_a, reference)
    xy_b = latlon_to_local_xy(coords_b, reference)

    min_distance = None
    for ax, ay in xy_a:
        for bx, by in xy_b:
            distance = math.hypot(ax - bx, ay - by)
            if min_distance is None or distance < min_distance:
                min_distance = distance
    return min_distance


def fetch_text(url):
    response = SESSION.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def fetch_way_history_xml(way_id):
    return fetch_text(f"https://api.openstreetmap.org/api/0.6/way/{way_id}/history")


def fetch_node_history_xml(node_id):
    return fetch_text(f"https://api.openstreetmap.org/api/0.6/node/{node_id}/history")


def parse_way_history(xml_text):
    root = ET.fromstring(xml_text)
    versions = []
    for way in root.findall("way"):
        versions.append(
            {
                "way_id": int(way.attrib["id"]),
                "version": int(way.attrib["version"]),
                "visible": way.attrib.get("visible", "true").lower() == "true",
                "timestamp": parse_timestamp(way.attrib.get("timestamp")),
                "changeset": int(way.attrib["changeset"]) if way.attrib.get("changeset") else None,
                "uid": int(way.attrib["uid"]) if way.attrib.get("uid") else None,
                "user": way.attrib.get("user") or "unknown",
                "node_refs": [nd.attrib["ref"] for nd in way.findall("nd")],
                "tags": {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")},
            }
        )
    versions.sort(key=lambda item: item["version"])
    return versions


def extract_node_coords(node):
    lat = node.attrib.get("lat")
    lon = node.attrib.get("lon")
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def parse_node_history(xml_text):
    root = ET.fromstring(xml_text)
    history = []
    for node in root.findall("node"):
        history.append(
            {
                "timestamp": parse_timestamp(node.attrib.get("timestamp")),
                "coords": extract_node_coords(node),
            }
        )
    history.sort(key=lambda item: item["timestamp"])
    return history


def fetch_all_node_histories(node_ids):
    unique_ids = list(dict.fromkeys(node_ids))
    histories = {}
    total = len(unique_ids)
    for index, node_id in enumerate(unique_ids, start=1):
        if index == 1 or index % 25 == 0 or index == total:
            print(f"Fetching node histories: {index}/{total}")
        histories[node_id] = parse_node_history(fetch_node_history_xml(node_id))
    return histories


def resolve_node_coords(node_history, at_timestamp):
    if not node_history:
        return None, "missing"

    chosen = None
    fallback = None
    for entry in node_history:
        if entry["coords"] is not None and fallback is None:
            fallback = entry["coords"]
        timestamp = entry["timestamp"]
        if pd.isna(timestamp):
            continue
        if timestamp <= at_timestamp and entry["coords"] is not None:
            chosen = entry["coords"]
        elif timestamp > at_timestamp:
            break

    if chosen is not None:
        return chosen, "historical"
    if fallback is not None:
        return fallback, "fallback"
    return None, "missing"


def diff_tags(previous_tags, current_tags):
    if previous_tags is None:
        return [f"+ {key}={value}" for key, value in sorted(current_tags.items())]

    changes = []
    previous_keys = set(previous_tags)
    current_keys = set(current_tags)

    for key in sorted(current_keys - previous_keys):
        changes.append(f"+ {key}={current_tags[key]}")
    for key in sorted(previous_keys - current_keys):
        changes.append(f"- {key} (was {previous_tags[key]})")
    for key in sorted(current_keys & previous_keys):
        if current_tags[key] != previous_tags[key]:
            changes.append(f"~ {key}: {previous_tags[key]} -> {current_tags[key]}")
    return changes


def classify_edit(previous_version, current_version, tag_changes):
    if previous_version is None:
        return "creation"

    geometry_changed = current_version["node_refs"] != previous_version["node_refs"]
    visibility_changed = current_version["visible"] != previous_version["visible"]
    tag_changed = bool(tag_changes)

    if visibility_changed:
        return "visibility change"
    if geometry_changed and tag_changed:
        return "geometry + tag change"
    if geometry_changed:
        return "geometry change"
    if tag_changed:
        return "tag change"
    return "no structural change"


def build_way_dataframe(versions, node_histories):
    rows = []
    geometries = {}
    previous_version = None
    previous_tags = None

    for version in versions:
        coords = []
        historical_hits = 0
        fallback_hits = 0
        missing_hits = 0

        for node_id in version["node_refs"]:
            coord, source = resolve_node_coords(node_histories.get(node_id, []), version["timestamp"])
            if coord is None:
                missing_hits += 1
                continue
            coords.append(coord)
            if source == "historical":
                historical_hits += 1
            else:
                fallback_hits += 1

        geometries[version["version"]] = coords
        is_closed = len(version["node_refs"]) >= 3 and version["node_refs"][0] == version["node_refs"][-1]

        perimeter_m = None
        area_sq_m = None
        if is_closed and len(coords) >= 3:
            perimeter_m = polygon_perimeter_m(coords)
            area_sq_m = polygon_area_sq_m(coords)
        elif len(coords) >= 2:
            perimeter_m = polyline_length_m(coords)

        tag_changes = diff_tags(previous_tags, version["tags"])
        edit_type = classify_edit(previous_version, version, tag_changes)
        geometry_changed = previous_version is not None and version["node_refs"] != previous_version["node_refs"]
        tag_changed = previous_version is not None and bool(tag_changes)

        if previous_version is None:
            node_change = "initial geometry"
        elif geometry_changed:
            node_change = "geometry changed"
        else:
            node_change = "geometry unchanged"

        if missing_hits > 0:
            geometry_reconstruction = "incomplete"
        elif fallback_hits > 0:
            geometry_reconstruction = "mixed fallback"
        else:
            geometry_reconstruction = "historical"

        rows.append(
            {
                "way_id": version["way_id"],
                "way_label": way_label(version["way_id"]),
                "role": way_meta(version["way_id"])["role"],
                "version": version["version"],
                "timestamp": version["timestamp"],
                "changeset": version["changeset"],
                "user": version["user"],
                "uid": version["uid"],
                "visible": version["visible"],
                "node_count": len(version["node_refs"]),
                "closed_way": is_closed,
                "perimeter_m": perimeter_m,
                "area_sq_m": area_sq_m,
                "node_change": node_change,
                "geometry_changed": geometry_changed,
                "tag_changed": tag_changed,
                "edit_type": edit_type,
                "tag_change_count": len(tag_changes),
                "tag_changes": " | ".join(tag_changes) if tag_changes else "no tag changes",
                "tags": "; ".join(f"{key}={value}" for key, value in sorted(version["tags"].items())),
                "geometry_reconstruction": geometry_reconstruction,
                "historical_node_hits": historical_hits,
                "fallback_node_hits": fallback_hits,
                "missing_node_count": missing_hits,
            }
        )

        previous_version = version
        previous_tags = version["tags"]

    df = pd.DataFrame(rows)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df, geometries


def get_milestones(df, strike_timestamp):
    ordered = df.sort_values(["timestamp", "version"]).reset_index(drop=True)
    pre = ordered[ordered["timestamp"] < strike_timestamp]
    post = ordered[ordered["timestamp"] >= strike_timestamp]
    return {
        "first_version": ordered.iloc[0].copy(),
        "last_pre_strike": pre.iloc[-1].copy() if not pre.empty else None,
        "first_post_strike": post.iloc[0].copy() if not post.empty else None,
        "latest": ordered.iloc[-1].copy(),
    }


def extract_major_tag_events(df):
    events = []
    keywords = ("school", "education", "military", "landuse", "name")
    for row in df.itertuples(index=False):
        if row.tag_changes == "no tag changes":
            continue
        lowered = row.tag_changes.lower()
        if any(keyword in lowered for keyword in keywords):
            events.append(f"{format_timestamp(row.timestamp)} v{row.version}: {row.tag_changes}")
    return events[:6]


def summarise_way(df, milestones, strike_timestamp):
    perimeter_series = df["perimeter_m"].dropna()
    summary = {
        "way_id": int(df["way_id"].iloc[0]),
        "way_label": df["way_label"].iloc[0],
        "role": df["role"].iloc[0],
        "versions": int(len(df)),
        "first_version": int(df["version"].min()),
        "latest_version": int(df["version"].max()),
        "first_timestamp": df["timestamp"].min(),
        "latest_timestamp": df["timestamp"].max(),
        "existed_pre_strike": milestones["last_pre_strike"] is not None,
        "geometry_change_count": int(df["geometry_changed"].sum()),
        "tag_change_count": int(df["tag_changed"].sum()),
        "geometry_changes_pre_strike": int(df.loc[df["timestamp"] < strike_timestamp, "geometry_changed"].sum()),
        "geometry_changes_post_strike": int(df.loc[df["timestamp"] >= strike_timestamp, "geometry_changed"].sum()),
        "tag_changes_pre_strike": int(df.loc[df["timestamp"] < strike_timestamp, "tag_changed"].sum()),
        "tag_changes_post_strike": int(df.loc[df["timestamp"] >= strike_timestamp, "tag_changed"].sum()),
        "major_tag_events": " || ".join(extract_major_tag_events(df)),
        "first_perimeter_m": safe_float(perimeter_series.iloc[0]) if not perimeter_series.empty else None,
        "latest_perimeter_m": safe_float(perimeter_series.iloc[-1]) if not perimeter_series.empty else None,
        "delta_perimeter_m": safe_float(perimeter_series.iloc[-1] - perimeter_series.iloc[0]) if len(perimeter_series) >= 2 else 0.0,
    }

    for milestone_key in MILESTONE_ORDER:
        row = milestones[milestone_key]
        summary[f"{milestone_key}_version"] = int(row["version"]) if row is not None else None
        summary[f"{milestone_key}_timestamp"] = row["timestamp"] if row is not None else pd.NaT
        summary[f"{milestone_key}_perimeter_m"] = safe_float(row["perimeter_m"]) if row is not None else None
    return summary


def milestone_record(way_id, row, milestone_key):
    record = {
        "way_id": way_id,
        "way_label": way_label(way_id),
        "role": way_meta(way_id)["role"],
        "milestone_key": milestone_key,
        "milestone_label": MILESTONE_LABELS[milestone_key],
        "available": row is not None,
    }
    if row is None:
        return record

    record.update(
        {
            "version": int(row["version"]),
            "timestamp": format_timestamp(row["timestamp"]),
            "node_count": int(row["node_count"]),
            "closed_way": bool(row["closed_way"]),
            "perimeter_m": f"{row['perimeter_m']:.2f}" if pd.notna(row["perimeter_m"]) else "n/a",
            "edit_type": row["edit_type"],
            "tag_changes": compact_text(row["tag_changes"], 120),
            "tags": compact_text(row["tags"], 150),
            "geometry_reconstruction": row["geometry_reconstruction"],
        }
    )
    return record


def build_milestone_comparison_df(all_milestones):
    rows = []
    for way_id in WAY_IDS:
        for milestone_key in MILESTONE_ORDER:
            rows.append(milestone_record(way_id, all_milestones[way_id][milestone_key], milestone_key))
    return pd.DataFrame(rows)


def expand_bounds(coords, padding_ratio=0.15, min_padding_deg=0.001):
    lats = [lat for lat, _ in coords]
    lons = [lon for _, lon in coords]
    min_lat = min(lats)
    max_lat = max(lats)
    min_lon = min(lons)
    max_lon = max(lons)

    lat_pad = max((max_lat - min_lat) * padding_ratio, min_padding_deg)
    lon_pad = max((max_lon - min_lon) * padding_ratio, min_padding_deg)
    return min_lat - lat_pad, max_lat + lat_pad, min_lon - lon_pad, max_lon + lon_pad


def latlon_to_tile_fraction(lat, lon, zoom):
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def choose_zoom(bounds, max_tile_count=16, max_zoom=19):
    min_lat, max_lat, min_lon, max_lon = bounds
    for zoom in range(max_zoom, -1, -1):
        min_x, max_y = latlon_to_tile_fraction(min_lat, min_lon, zoom)
        max_x, min_y = latlon_to_tile_fraction(max_lat, max_lon, zoom)
        tile_width = int(math.floor(max_x) - math.floor(min_x) + 1)
        tile_height = int(math.floor(max_y) - math.floor(min_y) + 1)
        if tile_width * tile_height <= max_tile_count:
            return zoom
    return 0


def fetch_osm_tile(zoom, x_tile, y_tile):
    cache_key = (zoom, x_tile, y_tile)
    cached_tile = TILE_CACHE.get(cache_key)
    if cached_tile is not None:
        return cached_tile.copy()

    response = SESSION.get(f"https://tile.openstreetmap.org/{zoom}/{x_tile}/{y_tile}.png", timeout=60)
    response.raise_for_status()
    tile = Image.open(io.BytesIO(response.content)).convert("RGB")
    TILE_CACHE[cache_key] = tile
    return tile.copy()


def build_basemap(bounds):
    zoom = choose_zoom(bounds)
    min_lat, max_lat, min_lon, max_lon = bounds
    min_x, max_y = latlon_to_tile_fraction(min_lat, min_lon, zoom)
    max_x, min_y = latlon_to_tile_fraction(max_lat, max_lon, zoom)

    min_tile_x = int(math.floor(min_x))
    max_tile_x = int(math.floor(max_x))
    min_tile_y = int(math.floor(min_y))
    max_tile_y = int(math.floor(max_y))

    tile_width = max_tile_x - min_tile_x + 1
    tile_height = max_tile_y - min_tile_y + 1
    image = Image.new("RGB", (tile_width * 256, tile_height * 256), color=(240, 240, 240))

    warning_shown = False
    for x_tile in range(min_tile_x, max_tile_x + 1):
        for y_tile in range(min_tile_y, max_tile_y + 1):
            try:
                tile = fetch_osm_tile(zoom, x_tile, y_tile)
            except requests.RequestException:
                if not warning_shown:
                    print("Warning: some OpenStreetMap tiles could not be fetched; blank fallback tiles are used.")
                    warning_shown = True
                continue
            image.paste(tile, ((x_tile - min_tile_x) * 256, (y_tile - min_tile_y) * 256))

    return np.asarray(image), {
        "zoom": zoom,
        "min_tile_x": min_tile_x,
        "min_tile_y": min_tile_y,
        "width_px": image.width,
        "height_px": image.height,
    }


def latlon_to_basemap_pixels(lat, lon, basemap_info):
    x_tile, y_tile = latlon_to_tile_fraction(lat, lon, basemap_info["zoom"])
    return (
        (x_tile - basemap_info["min_tile_x"]) * 256,
        (y_tile - basemap_info["min_tile_y"]) * 256,
    )


def geometry_to_pixel_points(coords, basemap_info):
    return [latlon_to_basemap_pixels(lat, lon, basemap_info) for lat, lon in coords]


def save_note_figure(output_path, title, message, extra_lines=None):
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.axis("off")
    ax.text(0.03, 0.86, title, fontsize=16, fontweight="bold", ha="left", va="top")
    ax.text(0.03, 0.58, textwrap.fill(message, 82), fontsize=11.5, ha="left", va="top")
    if extra_lines:
        y = 0.34
        for line in extra_lines:
            ax.text(0.05, y, f"- {line}", fontsize=10.5, ha="left", va="top")
            y -= 0.1
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def plot_way_milestone_overlay(way_id, geometries, milestones, output_path):
    selected = []
    seen_versions = {}
    for milestone_key in MILESTONE_ORDER:
        row = milestones[milestone_key]
        if row is None:
            continue
        version = int(row["version"])
        coords = geometries.get(version, [])
        if len(coords) < 2:
            continue
        if version in seen_versions:
            seen_versions[version]["labels"].append(MILESTONE_LABELS[milestone_key])
            continue
        seen_versions[version] = {
            "version": version,
            "milestone_key": milestone_key,
            "labels": [MILESTONE_LABELS[milestone_key]],
            "coords": coords,
        }
    selected = [seen_versions[version] for version in sorted(seen_versions)]

    if not selected:
        save_note_figure(output_path, f"{way_display_label(way_id)} milestone overlay", "No geometry could be reconstructed for the selected milestone states.")
        return

    all_coords = [point for item in selected for point in item["coords"]]
    basemap_image, basemap_info = build_basemap(expand_bounds(all_coords, padding_ratio=0.18))

    fig, ax = plt.subplots(figsize=(9, 9))
    ax.imshow(basemap_image, origin="upper")
    legend_handles = []

    for item in selected:
        style = MILESTONE_STYLES[item["milestone_key"]]
        coords = item["coords"]
        points = geometry_to_pixel_points(close_coords(coords) if len(coords) >= 3 else coords, basemap_info)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        if len(coords) >= 3:
            ax.fill(xs, ys, color=style["color"], alpha=0.08)
        ax.plot(xs, ys, color=style["color"], linestyle=style["linestyle"], linewidth=style["linewidth"], alpha=0.95)
        legend_handles.append(
            Line2D([0], [0], color=style["color"], linestyle=style["linestyle"], linewidth=style["linewidth"], label=f"v{item['version']} - {' / '.join(item['labels'])}")
        )

    latest = milestones["latest"]
    latest_coords = geometries.get(int(latest["version"]), []) if latest is not None else []
    centroid = geometry_centroid(latest_coords)
    if centroid is not None:
        x_px, y_px = latlon_to_basemap_pixels(centroid[0], centroid[1], basemap_info)
        ax.text(x_px, y_px, way_annotation_label(way_id), fontsize=8.5, ha="center", va="center", bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": way_color_hex(way_id), "alpha": 0.92})

    ax.set_title(f"{way_label(way_id)} (Way {way_id}) milestone overlay")
    ax.set_xlim(0, basemap_info["width_px"])
    ax.set_ylim(basemap_info["height_px"], 0)
    ax.set_axis_off()
    ax.legend(handles=legend_handles, loc="upper right", fontsize=8, framealpha=0.95, title="Milestones")
    fig.text(0.01, 0.01, "Basemap: OpenStreetMap", fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def create_state_map(output_path, selection, title, subtitle):
    available = {way_id: coords for way_id, coords in selection.items() if len(coords) >= 2}
    if not available:
        save_note_figure(output_path, title, "No geometry was available for this state.", [subtitle] if subtitle else None)
        return

    all_coords = [point for coords in available.values() for point in coords]
    basemap_image, basemap_info = build_basemap(expand_bounds(all_coords, padding_ratio=0.18))
    fig, ax = plt.subplots(figsize=(9, 9))
    ax.imshow(basemap_image, origin="upper")
    legend_handles = []

    for way_id, coords in sorted(available.items()):
        plot_coords = close_coords(coords) if len(coords) >= 3 else coords
        points = geometry_to_pixel_points(plot_coords, basemap_info)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        color = way_color_hex(way_id)
        if len(coords) >= 3:
            ax.fill(xs, ys, color=color, alpha=0.14)
        ax.plot(xs, ys, color=color, linewidth=3, alpha=0.95)
        centroid = geometry_centroid(coords)
        if centroid is not None:
            x_px, y_px = latlon_to_basemap_pixels(centroid[0], centroid[1], basemap_info)
            ax.text(x_px, y_px, way_annotation_label(way_id), fontsize=8.5, ha="center", va="center", bbox={"boxstyle": "round,pad=0.22", "facecolor": "white", "edgecolor": color, "alpha": 0.92})
        legend_handles.append(Line2D([0], [0], color=color, linewidth=3, label=way_display_label(way_id)))

    ax.set_title(title)
    if subtitle:
        wrapped_subtitle = textwrap.fill(subtitle, width=62)
        ax.text(0.01, 0.99, wrapped_subtitle, transform=ax.transAxes, fontsize=9, ha="left", va="top", bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "alpha": 0.92, "edgecolor": "#888888"})
    ax.set_xlim(0, basemap_info["width_px"])
    ax.set_ylim(basemap_info["height_px"], 0)
    ax.set_axis_off()
    ax.legend(handles=legend_handles, loc="upper right", framealpha=0.95, fontsize=8)
    fig.text(0.01, 0.01, "Basemap: OpenStreetMap", fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def generate_state_maps(all_geometries, all_milestones, output_dir):
    state_dir = output_dir / "state_maps"
    state_dir.mkdir(exist_ok=True)
    outputs = []

    for milestone_key in MILESTONE_ORDER:
        combined_selection = {}
        labels = []
        for way_id in WAY_IDS:
            row = all_milestones[way_id][milestone_key]
            coords = all_geometries[way_id].get(int(row["version"]), []) if row is not None else []
            if len(coords) >= 2:
                combined_selection[way_id] = coords
                labels.append(way_display_label(way_id))

            output_path = state_dir / f"{milestone_key}_way_{way_id}.png"
            create_state_map(output_path, {way_id: coords} if coords else {}, f"{MILESTONE_LABELS[milestone_key]} map: {way_label(way_id)} (Way {way_id})", "")
            outputs.append(output_path)

        combined_path = state_dir / f"{milestone_key}_combined.png"
        subtitle = f"Included ways: {', '.join(labels)}" if labels else ""
        create_state_map(combined_path, combined_selection, f"{MILESTONE_LABELS[milestone_key]} combined map", subtitle)
        outputs.append(combined_path)

    latest_selection = {}
    latest_labels = []
    for way_id in WAY_IDS:
        latest = all_milestones[way_id]["latest"]
        coords = all_geometries[way_id].get(int(latest["version"]), []) if latest is not None else []
        if len(coords) >= 2:
            latest_selection[way_id] = coords
            latest_labels.append(way_display_label(way_id))

    latest_path = output_dir / "combined_latest_overlay.png"
    create_state_map(latest_path, latest_selection, "Combined latest geometry overlay", f"Included ways: {', '.join(latest_labels)}" if latest_labels else "")
    outputs.append(latest_path)
    return outputs


def plot_way_timeline(df, way_id, strike_timestamp, output_path):
    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.plot(df["timestamp"], df["version"], color=way_color_hex(way_id), linewidth=1.5, alpha=0.35)

    for edit_type, style in EDIT_TYPE_STYLES.items():
        subset = df[df["edit_type"] == edit_type]
        if subset.empty:
            continue
        ax.scatter(subset["timestamp"], subset["version"], marker=style["marker"], s=70, color=style["color"], edgecolors="white", linewidths=0.7, label=edit_type, zorder=3)

    milestones = get_milestones(df, strike_timestamp)
    for milestone_key in MILESTONE_ORDER:
        row = milestones[milestone_key]
        if row is None:
            continue
        ax.annotate(MILESTONE_LABELS[milestone_key], xy=(row["timestamp"], row["version"]), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8, bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "#999999", "alpha": 0.9})

    ax.axvline(strike_timestamp, color="#D32F2F", linestyle="--", linewidth=1.6, label="Strike date")
    ax.set_title(f"Edit timeline: {way_label(way_id)} (Way {way_id})")
    ax.set_ylabel("Version")
    ax.set_xlabel("Timestamp (UTC)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    ax.grid(True, linestyle=":", alpha=0.4)
    ax.legend(loc="upper left", fontsize=8, ncol=3, framealpha=0.95)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def plot_combined_timeline(all_dfs, all_milestones, strike_timestamp, output_path):
    fig, ax = plt.subplots(figsize=(12, 4.6))
    y_positions = {way_id: index for index, way_id in enumerate(WAY_IDS)}

    for way_id in WAY_IDS:
        df = all_dfs[way_id]
        y_value = y_positions[way_id]
        for edit_type, style in EDIT_TYPE_STYLES.items():
            subset = df[df["edit_type"] == edit_type]
            if subset.empty:
                continue
            ax.scatter(subset["timestamp"], [y_value] * len(subset), marker=style["marker"], color=way_color_hex(way_id), edgecolors="white", linewidths=0.7, s=90, alpha=0.95)

        for milestone_key in MILESTONE_ORDER:
            row = all_milestones[way_id][milestone_key]
            if row is None:
                continue
            ax.annotate(MILESTONE_LABELS[milestone_key], xy=(row["timestamp"], y_value), xytext=(0, 11), textcoords="offset points", ha="center", fontsize=7, bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "edgecolor": "#BBBBBB", "alpha": 0.9})

    way_handles = [Line2D([0], [0], marker="o", color="none", markerfacecolor=way_color_hex(way_id), markersize=9, label=way_display_label(way_id)) for way_id in WAY_IDS]
    type_handles = [Line2D([0], [0], marker=style["marker"], color="none", markerfacecolor=style["color"], markersize=8, label=edit_type) for edit_type, style in EDIT_TYPE_STYLES.items()]

    ax.axvline(strike_timestamp, color="#D32F2F", linestyle="--", linewidth=1.6)
    ax.set_title("Combined edit timeline by way")
    ax.set_xlabel("Timestamp (UTC)")
    ax.set_yticks([y_positions[way_id] for way_id in WAY_IDS], [way_display_label(way_id) for way_id in WAY_IDS])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    ax.grid(True, axis="x", linestyle=":", alpha=0.4)
    ax.legend(handles=way_handles + type_handles, fontsize=8, ncol=2, loc="upper left", framealpha=0.95)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def plot_perimeter_timeseries(all_dfs, strike_timestamp, output_path):
    fig, ax = plt.subplots(figsize=(11, 5))
    for way_id in WAY_IDS:
        series = all_dfs[way_id].dropna(subset=["perimeter_m"])
        if series.empty:
            continue
        ax.plot(series["timestamp"], series["perimeter_m"], marker="o", linewidth=2, markersize=5, color=way_color_hex(way_id), label=way_display_label(way_id))

    ax.axvline(strike_timestamp, color="#D32F2F", linestyle="--", linewidth=1.6, label="Strike date")
    ax.set_title("Perimeter / length evolution by way")
    ax.set_ylabel("Metres")
    ax.set_xlabel("Timestamp (UTC)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    ax.grid(True, linestyle=":", alpha=0.4)
    ax.legend(framealpha=0.95)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def build_animation_timeline(all_histories):
    events_by_time = {}
    for way_id, versions in all_histories.items():
        for version in versions:
            timestamp = version["timestamp"]
            if pd.isna(timestamp):
                continue
            events_by_time.setdefault(timestamp, []).append((way_id, version["version"]))

    frames = []
    current_versions = {}
    for timestamp in sorted(events_by_time):
        for way_id, version_number in sorted(events_by_time[timestamp], key=lambda item: (item[0], item[1])):
            current_versions[way_id] = version_number
        frames.append({"timestamp": timestamp, "versions": current_versions.copy()})
    return frames


def draw_box(draw, bounds, fill, outline=None, radius=10, width=2):
    draw.rounded_rectangle(bounds, radius=radius, fill=fill, outline=outline, width=width)


def draw_text_block(draw, top_left, lines, fill=(20, 20, 20, 255), line_spacing=4):
    x, y = top_left
    for line in lines:
        draw.text((x, y), line, fill=fill)
        bbox = draw.textbbox((x, y), line)
        y = bbox[3] + line_spacing


def timeline_x_for_timestamp(timestamp, start_ts, end_ts, x0, x1):
    if end_ts <= start_ts:
        return x0
    ratio = (timestamp - start_ts).total_seconds() / (end_ts - start_ts).total_seconds()
    ratio = max(0.0, min(1.0, ratio))
    return x0 + ratio * (x1 - x0)


def add_animation_annotations(frame, frame_state, strike_timestamp, timeline_start, timeline_end):
    overlay = Image.new("RGBA", frame.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    header_lines = [
        "OSM way version update animation",
        f"Current frame: {frame_state['timestamp'].strftime('%Y-%m-%d %H:%M:%S UTC')}",
    ]
    draw_box(draw, (18, 18, 420, 80), fill=(255, 255, 255, 225), outline=(80, 80, 80, 180))
    draw_text_block(draw, (32, 30), header_lines)

    legend_left = frame.size[0] - 280
    legend_height = 36 + (len(WAY_IDS) * 22)
    draw_box(
        draw,
        (legend_left, 18, frame.size[0] - 18, 18 + legend_height),
        fill=(255, 255, 255, 225),
        outline=(80, 80, 80, 180),
    )
    draw.text((legend_left + 16, 30), "Current versions", fill=(20, 20, 20, 255))
    y = 52
    for way_id in WAY_IDS:
        color = hex_to_rgb(way_color_hex(way_id))
        version_number = frame_state["versions"].get(way_id)
        draw.rectangle((legend_left + 16, y + 4, legend_left + 34, y + 20), fill=color + (255,), outline=(255, 255, 255, 255))
        state_text = (
            f"{way_display_label(way_id)} not present yet"
            if version_number is None
            else f"{way_display_label(way_id)} v{version_number}"
        )
        draw.text((legend_left + 44, y + 1), state_text, fill=(20, 20, 20, 255))
        y += 22

    panel_height = 112
    panel_bounds = (18, frame.size[1] - panel_height - 18, frame.size[0] - 18, frame.size[1] - 18)
    draw_box(draw, panel_bounds, fill=(255, 255, 255, 232), outline=(80, 80, 80, 180))

    panel_left, panel_top, panel_right, panel_bottom = panel_bounds
    timeline_left = panel_left + 48
    timeline_right = panel_right - 48
    timeline_y = panel_top + 58
    label_y = panel_top + 16
    date_y = panel_top + 70

    current_x = timeline_x_for_timestamp(frame_state["timestamp"], timeline_start, timeline_end, timeline_left, timeline_right)
    strike_x = timeline_x_for_timestamp(strike_timestamp, timeline_start, timeline_end, timeline_left, timeline_right)

    draw.text((timeline_left, panel_top + 10), "Timeline", fill=(20, 20, 20, 255))
    draw.line((timeline_left, timeline_y, timeline_right, timeline_y), fill=(145, 145, 145, 255), width=5)
    draw.line((timeline_left, timeline_y, current_x, timeline_y), fill=(21, 101, 192, 255), width=7)

    for tick_time in sorted({timeline_start, strike_timestamp, timeline_end}):
        tick_x = timeline_x_for_timestamp(tick_time, timeline_start, timeline_end, timeline_left, timeline_right)
        tick_fill = (198, 40, 40, 255) if tick_time == strike_timestamp else (100, 100, 100, 255)
        draw.line((tick_x, timeline_y - 11, tick_x, timeline_y + 11), fill=tick_fill, width=3)

    start_label = pd.Timestamp(timeline_start).strftime("%Y-%m-%d")
    end_label = pd.Timestamp(timeline_end).strftime("%Y-%m-%d")
    draw.text((timeline_left - 2, date_y), start_label, fill=(100, 100, 100, 255))
    end_bbox = draw.textbbox((0, 0), end_label)
    draw.text((timeline_right - (end_bbox[2] - end_bbox[0]) + 2, date_y), end_label, fill=(100, 100, 100, 255))

    event_ticks = sorted(
        {
            timeline_x_for_timestamp(frame_state_item["timestamp"], timeline_start, timeline_end, timeline_left, timeline_right)
            for frame_state_item in build_animation_timeline_cache
        }
    )
    for tick_x in event_ticks:
        draw.line((tick_x, timeline_y - 4, tick_x, timeline_y + 4), fill=(115, 115, 115, 170), width=1)

    draw.ellipse((current_x - 7, timeline_y - 7, current_x + 7, timeline_y + 7), fill=(13, 71, 161, 255), outline=(255, 255, 255, 255), width=2)
    current_label = frame_state["timestamp"].strftime("%Y-%m-%d")
    current_bbox = draw.textbbox((0, 0), current_label)
    current_label_x = max(panel_left + 12, min(current_x - (current_bbox[2] - current_bbox[0]) / 2, panel_right - (current_bbox[2] - current_bbox[0]) - 12))
    draw.text((current_label_x, panel_top + 34), current_label, fill=(13, 71, 161, 255))

    if timeline_start < strike_timestamp < timeline_end:
        pre_label = "PRE-STRIKE"
        post_label = "POST-STRIKE"
        pre_bbox = draw.textbbox((0, 0), pre_label)
        post_bbox = draw.textbbox((0, 0), post_label)
        pre_x = max(timeline_left, ((timeline_left + strike_x) / 2) - (pre_bbox[2] - pre_bbox[0]) / 2)
        post_x = min(timeline_right - (post_bbox[2] - post_bbox[0]), ((strike_x + timeline_right) / 2) - (post_bbox[2] - post_bbox[0]) / 2)
        draw.text((pre_x, label_y), pre_label, fill=(198, 40, 40, 255))
        draw.text((post_x, label_y), post_label, fill=(198, 40, 40, 255))
        strike_label = f"Strike date {pd.Timestamp(strike_timestamp).strftime('%Y-%m-%d')}"
        strike_bbox = draw.textbbox((0, 0), strike_label)
        strike_label_x = max(panel_left + 12, min(strike_x + 8, panel_right - (strike_bbox[2] - strike_bbox[0]) - 12))
        draw.text((strike_label_x, panel_top + 34), strike_label, fill=(198, 40, 40, 255))
    elif strike_timestamp <= timeline_start:
        draw.text((timeline_left, label_y), "POST-STRIKE ONLY", fill=(198, 40, 40, 255))
    else:
        draw.text((timeline_left, label_y), "PRE-STRIKE ONLY", fill=(198, 40, 40, 255))

    return Image.alpha_composite(frame, overlay)


build_animation_timeline_cache = []


def render_animation_frame(basemap_image, basemap_info, frame_state, all_geometries, strike_timestamp, timeline_start, timeline_end):
    frame = Image.fromarray(basemap_image).convert("RGBA")
    geometry_overlay = Image.new("RGBA", frame.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(geometry_overlay)

    for way_id in WAY_IDS:
        version_number = frame_state["versions"].get(way_id)
        if version_number is None:
            continue
        coords = all_geometries.get(way_id, {}).get(version_number, [])
        if len(coords) < 2:
            continue

        plot_coords = close_coords(coords) if len(coords) >= 3 else coords
        points = geometry_to_pixel_points(plot_coords, basemap_info)
        color = hex_to_rgb(way_color_hex(way_id))
        if len(coords) >= 3:
            draw.polygon(points, fill=color + (58,))
        draw.line(points, fill=color + (235,), width=6)

    frame = Image.alpha_composite(frame, geometry_overlay)
    return add_animation_annotations(frame, frame_state, strike_timestamp, timeline_start, timeline_end)


def create_way_history_animation(all_histories, all_geometries, strike_timestamp, output_path):
    global build_animation_timeline_cache
    timeline = build_animation_timeline(all_histories)
    build_animation_timeline_cache = timeline
    if not timeline:
        return None

    all_coords = []
    for geometries in all_geometries.values():
        for coords in geometries.values():
            if len(coords) >= 2:
                all_coords.extend(coords)
    if not all_coords:
        return None

    basemap_image, basemap_info = build_basemap(expand_bounds(all_coords, padding_ratio=0.18))
    timeline_start = timeline[0]["timestamp"]
    timeline_end = timeline[-1]["timestamp"]

    frames = []
    durations = []
    for index, frame_state in enumerate(timeline, start=1):
        print(f"Rendering animation frame {index}/{len(timeline)}...")
        frame = render_animation_frame(
            basemap_image,
            basemap_info,
            frame_state,
            all_geometries,
            strike_timestamp,
            timeline_start,
            timeline_end,
        )
        frames.append(frame.convert("P", palette=Image.ADAPTIVE))
        durations.append(900)

    if durations:
        durations[-1] = 2200

    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
    )
    return output_path


def build_conflation_assessment(all_milestones, all_geometries):
    school_id = 1484791929
    compound_id = 1485767423
    barracks_id = 942760673

    school_pre = all_milestones[school_id]["last_pre_strike"]
    compound_pre = all_milestones[compound_id]["last_pre_strike"]
    barracks_pre = all_milestones[barracks_id]["last_pre_strike"]

    school_latest = all_milestones[school_id]["latest"]
    compound_latest = all_milestones[compound_id]["latest"]
    barracks_latest = all_milestones[barracks_id]["latest"]

    indicators = []
    score = 20

    if school_pre is None:
        indicators.append({"label": "Separate pre-strike school polygon", "status": "risk", "detail": "No separate school way is present before the strike date in this OSM history."})
        score += 30
    else:
        indicators.append({"label": "Separate pre-strike school polygon", "status": "clear", "detail": "A separate school polygon exists before the strike date."})
        score -= 10

    if compound_pre is None:
        indicators.append({"label": "Separate pre-strike compound polygon", "status": "risk", "detail": "No smaller adjacent compound way is present before the strike date."})
        score += 18
    else:
        indicators.append({"label": "Separate pre-strike compound polygon", "status": "clear", "detail": "A distinct smaller compound polygon exists before the strike date."})
        score -= 8

    if barracks_pre is None:
        indicators.append({"label": "Broader pre-strike military-area polygon", "status": "unknown", "detail": "No broader military-area way was found before the strike date."})
    else:
        indicators.append({"label": "Broader pre-strike military-area polygon", "status": "ambiguous", "detail": "A broader military-tagged barracks area is present before the strike date."})
        score += 10

    if school_pre is None or compound_pre is None:
        indicators.append({"label": "Post-strike clarification visible", "status": "ambiguous", "detail": "The school or smaller compound polygons appear only after the strike, indicating later clarification of the OSM record."})
        score += 8

    latest_school_coords = all_geometries[school_id].get(int(school_latest["version"]), []) if school_latest is not None else []
    latest_compound_coords = all_geometries[compound_id].get(int(compound_latest["version"]), []) if compound_latest is not None else []
    latest_barracks_coords = all_geometries[barracks_id].get(int(barracks_latest["version"]), []) if barracks_latest is not None else []

    school_compound_distance = minimum_vertex_distance_m(latest_school_coords, latest_compound_coords)
    school_barracks_distance = minimum_vertex_distance_m(latest_school_coords, latest_barracks_coords)
    score = max(0, min(100, score))
    rating = "High" if score >= 65 else "Moderate" if score >= 40 else "Low"

    return {
        "score": score,
        "overall_rating": rating,
        "indicators": indicators,
        "latest_school_compound_distance_m": safe_float(school_compound_distance),
        "latest_school_barracks_distance_m": safe_float(school_barracks_distance),
    }


def plot_conflation_risk(conflation, output_path):
    fig, ax = plt.subplots(figsize=(11, 6.5))
    ax.axis("off")

    rating_color = {"Low": "#2E7D32", "Moderate": "#F9A825", "High": "#C62828"}[conflation["overall_rating"]]
    rating_box = FancyBboxPatch((0.03, 0.54), 0.28, 0.38, boxstyle="round,pad=0.02,rounding_size=0.02", facecolor=rating_color, edgecolor="#333333", linewidth=1.2, transform=ax.transAxes)
    ax.add_patch(rating_box)
    ax.text(0.17, 0.81, "Conflation Risk", fontsize=15, color="white", fontweight="bold", ha="center", transform=ax.transAxes)
    ax.text(0.17, 0.69, conflation["overall_rating"], fontsize=26, color="white", fontweight="bold", ha="center", transform=ax.transAxes)
    ax.text(0.17, 0.58, "Qualitative audit rating", fontsize=14, color="white", ha="center", transform=ax.transAxes)

    status_colors = {"clear": "#2E7D32", "ambiguous": "#F9A825", "risk": "#C62828", "unknown": "#607D8B"}
    ax.text(0.36, 0.93, "Traffic-light summary of map clarity before and after 28 February 2026", fontsize=12, fontweight="bold", ha="left", transform=ax.transAxes)

    y = 0.85
    for indicator in conflation["indicators"]:
        ax.scatter([0.38], [y], s=120, color=status_colors[indicator["status"]], transform=ax.transAxes)
        ax.text(0.41, y + 0.02, indicator["label"], fontsize=11, fontweight="bold", ha="left", va="top", transform=ax.transAxes)
        ax.text(0.41, y - 0.01, textwrap.fill(indicator["detail"], 70), fontsize=10, ha="left", va="top", transform=ax.transAxes)
        y -= 0.14

    footer_lines = []
    if conflation["latest_school_compound_distance_m"] is not None:
        footer_lines.append(f"Latest school-to-compound minimum vertex distance: {conflation['latest_school_compound_distance_m']:.1f} m")
    if conflation["latest_school_barracks_distance_m"] is not None:
        footer_lines.append(f"Latest school-to-barracks minimum vertex distance: {conflation['latest_school_barracks_distance_m']:.1f} m")
    if footer_lines:
        ax.text(0.03, 0.12, "\n".join(footer_lines), fontsize=10, ha="left", transform=ax.transAxes)

    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def build_way_narrative(summary, strike_timestamp):
    sentences = [
        f"{summary['way_label']} has {summary['versions']} recorded versions spanning {format_timestamp(summary['first_timestamp'])} to {format_timestamp(summary['latest_timestamp'])}.",
        f"Geometry changed {summary['geometry_change_count']} time(s) overall and {summary['geometry_changes_pre_strike']} time(s) before the strike divider.",
        f"Tag changes occurred {summary['tag_change_count']} time(s) overall and {summary['tag_changes_pre_strike']} time(s) before the strike divider.",
    ]

    if summary["existed_pre_strike"]:
        sentences.insert(1, f"It existed before the strike; the last pre-strike state is v{summary['last_pre_strike_version']} at {format_timestamp(summary['last_pre_strike_timestamp'])}.")
    else:
        sentences.insert(1, f"It does not appear before the strike divider ({format_timestamp(strike_timestamp)}); the first OSM version is post-strike.")

    if summary["latest_perimeter_m"] is not None:
        sentences.append(f"Its perimeter / line length changes from {summary['first_perimeter_m']:.2f} m to {summary['latest_perimeter_m']:.2f} m.")
    if summary["major_tag_events"]:
        sentences.append(f"Major semantic edits: {summary['major_tag_events']}.")
    return " ".join(sentences)


def generate_key_findings(summary_by_way, conflation, strike_timestamp):
    school = summary_by_way[1484791929]
    compound = summary_by_way[1485767423]
    barracks = summary_by_way[942760673]

    findings = []
    findings.append(
        f"The school polygon first appears on {format_timestamp(school['first_timestamp'])}, after the strike divider of {format_timestamp(strike_timestamp)}."
        if not school["existed_pre_strike"]
        else f"The school polygon existed before the strike; its last pre-strike version is v{school['last_pre_strike_version']}."
    )
    findings.append(
        f"The smaller suspected compound polygon also appears only after the strike, first at {format_timestamp(compound['first_timestamp'])}."
        if not compound["existed_pre_strike"]
        else f"The suspected compound polygon predates the strike; its last pre-strike version is v{compound['last_pre_strike_version']}."
    )
    if barracks["existed_pre_strike"]:
        findings.append("The broader barracks-area way predates the strike and remains the clearest pre-strike military-tagged perimeter in this OSM record.")
    findings.append(f"Overall OSM conflation-risk rating from these indicators: {conflation['overall_rating']}.")
    findings.append("Post-strike edits materially expand the school and compound record, which suggests the OSM map became more explicit after 28 February 2026.")
    return findings


def markdown_table(df):
    if df.empty:
        return "_No rows available._"
    columns = list(df.columns)
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    rows = []
    for _, row in df.iterrows():
        values = []
        for column in columns:
            value = row[column]
            text = "" if pd.isna(value) else str(value)
            values.append(text.replace("\n", "<br>").replace("|", "\\|"))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, divider] + rows)


def repo_relative(path, root_dir):
    absolute_path = path if path.is_absolute() else (root_dir / path)
    return absolute_path.resolve().relative_to(root_dir.resolve()).as_posix()


def json_ready(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return iso_timestamp(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        return safe_float(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def generate_readme(root_dir, output_dir, strike_timestamp, summary_df, summary_by_way, key_findings, conflation, generated_files):
    ways_rows = []
    milestone_rows = []
    for _, row in summary_df.sort_values("way_id").iterrows():
        ways_rows.append({"Way ID": int(row["way_id"]), "Label": row["way_label"], "Role": row["role"], "First version": f"v{int(row['first_version'])} ({format_timestamp(row['first_timestamp'])})", "Latest version": f"v{int(row['latest_version'])} ({format_timestamp(row['latest_timestamp'])})"})
        milestone_rows.append({"Way": row["way_label"], "Last pre-strike": f"v{int(row['last_pre_strike_version'])} ({format_timestamp(row['last_pre_strike_timestamp'])})" if pd.notna(row["last_pre_strike_timestamp"]) else "Not present", "First post-strike": f"v{int(row['first_post_strike_version'])} ({format_timestamp(row['first_post_strike_timestamp'])})" if pd.notna(row["first_post_strike_timestamp"]) else "Not present", "Latest": f"v{int(row['latest_version'])} ({format_timestamp(row['latest_timestamp'])})"})

    school = summary_by_way[1484791929]
    compound = summary_by_way[1485767423]
    barracks = summary_by_way[942760673]
    pre_strike_absence = []
    if not school["existed_pre_strike"]:
        pre_strike_absence.append("the school")
    if not compound["existed_pre_strike"]:
        pre_strike_absence.append("the smaller military base")

    if len(pre_strike_absence) == 2:
        pre_strike_absence_text = "the school and the smaller military base"
    elif len(pre_strike_absence) == 1:
        pre_strike_absence_text = pre_strike_absence[0]
    else:
        pre_strike_absence_text = "all three mapped objects"

    latest_distance_text = ""
    latest_distance = conflation.get("latest_school_compound_distance_m")
    if latest_distance is not None:
        if latest_distance < 1.0:
            latest_distance_text = " In the latest mapped state, the school and military-base polygons directly touch or abut in the OSM geometry."
        else:
            latest_distance_text = (
                f" In the latest mapped state, the minimum school-to-military-base vertex distance is about "
                f"{latest_distance:.1f} m."
            )

    important_outputs = sorted(repo_relative(path, root_dir) for path in generated_files if path.suffix.lower() in {".csv", ".json", ".txt", ".png", ".gif", ".md"})
    lines = [
        f"# {PROJECT_TITLE}",
        "",
        "## Short Context",
        "",
        SHORT_CONTEXT,
        "",
        "## Methodology",
        "",
        "OpenStreetMap (OSM) is a collaborative map database that stores geographic features as editable objects with version history. "
        "A **way** in OSM is an ordered list of nodes: it can represent a line, such as a road or wall, or a closed polygon, such as a school or compound perimeter.",
        "",
        "This repository queries the OSM API for the full history of the selected ways and their nodes, reconstructs each way's geometry at each known edit timestamp, and then compares four states for each object: first mapped, last pre-strike, first post-strike, and latest. "
        "The outputs focus on whether the school, the smaller military base, and the broader barracks area were mapped clearly and separately before **28 February 2026**.",
        "",
        "## Ways Analysed",
        "",
        markdown_table(pd.DataFrame(ways_rows)),
        "",
        "## Strike Date and Why It Matters",
        "",
        f"The analytical divider used by this run is **{format_timestamp(strike_timestamp)}**. The script labels the first version, last pre-strike version, first post-strike version, and latest version for each analysed way, then uses those states consistently in the tables and map exports.",
        "",
        "## Key Findings",
        "",
    ]
    for finding in key_findings:
        lines.append(f"- {finding}")

    lines.extend(
        [
            "",
            "## Timeline Visualisations",
            "",
            f"![Way history animation]({repo_relative(output_dir / 'way_history_animation.gif', root_dir)})",
            "",
            "This animation replays the mapped geometry changes through time. The bottom timeline shows that the broader barracks way already exists in the pre-strike period, while the school and smaller military-base polygons only enter the OSM record after the strike divider.",
            "",
            f"![Combined timeline]({repo_relative(output_dir / 'combined_timeline.png', root_dir)})",
            "",
            "The combined timeline compresses the edit history into discrete events by object. It shows that most school and smaller military-base edits cluster after 28 February 2026, whereas the barracks object has both pre-strike history and a second burst of post-strike refinement.",
            "",
            f"![Perimeter evolution]({repo_relative(output_dir / 'perimeter_comparison.png', root_dir)})",
            "",
            f"This chart tracks how each way's perimeter or line length changes over time. The barracks perimeter changes across pre-strike and post-strike states, while the school and smaller military-base perimeters only appear once those ways are created after the strike.",
            "",
            "## Geometry Overlays",
            "",
            f"![Combined latest geometry overlay]({repo_relative(output_dir / 'combined_latest_overlay.png', root_dir)})",
            "",
            f"This overlay shows the latest OSM state, where the School, Military base, and Barracks are mapped as separate named polygons rather than one undifferentiated area.{latest_distance_text}",
            "",
            f"![Last pre-strike combined map]({repo_relative(output_dir / 'state_maps' / 'last_pre_strike_combined.png', root_dir)})",
            "",
            f"This is the key pre-strike reference image. In the current history, {pre_strike_absence_text} are not yet present here as separate ways, while the broader barracks boundary remains the main military-tagged pre-strike polygon.",
            "",
            f"![First post-strike combined map]({repo_relative(output_dir / 'state_maps' / 'first_post_strike_combined.png', root_dir)})",
            "",
            "This panel shows the first available post-strike state for each way. It makes the map clarification visible by showing when the school and smaller military-base polygons first appear as distinct objects in OSM.",
            "",
            f"![Conflation risk summary]({repo_relative(output_dir / 'conflation_risk.png', root_dir)})",
            "",
            f"This figure turns the edit history into a brief interpretive summary. The current qualitative audit rating is {conflation['overall_rating']}, driven mainly by the existence of a broader pre-strike barracks perimeter and the post-strike arrival of more explicit school and smaller military-base mapping.",
            "",
            "## Pre-strike State Comparison",
            "",
            markdown_table(pd.DataFrame(milestone_rows)),
            "",
            "## Why the Edit History May Matter",
            "",
            f"The current qualitative audit rating for conflation risk is **{conflation['overall_rating']}**. In this repository, that means the degree to which the pre-strike map record was explicit enough to keep the school, the smaller adjacent compound, and the broader military area clearly distinct. This is an audit heuristic, not a legal or operational conclusion.",
            "",
            "## Caveats",
            "",
            "- Way history is fetched from the OpenStreetMap API for each configured way ID.",
            "- Geometry is reconstructed from node history by selecting the latest coordinate-bearing node version at or before each way version timestamp where possible.",
            "- Rows that required fallback geometry or had missing node coordinates are flagged in the CSV outputs.",
            "- This repository remains framed as post-incident mapping-history review for audit and explanation, not operational analysis.",
            "",
            "## Output Files",
            "",
        ]
    )
    for output in important_outputs:
        lines.append(f"- `{output}`")
    lines.extend(
        [
            "",
            "## Reproducibility / How to Run",
            "",
            "```bash",
            f"python main.py --strike-date {strike_timestamp.strftime('%Y-%m-%d')}",
            "```",
            "",
        ]
    )

    readme_path = root_dir / "README.md"
    readme_path.write_text("\n".join(lines), encoding="utf-8")
    return readme_path


def build_summary_json(strike_timestamp, summary_by_way, all_milestones, key_findings, conflation, generated_files, root_dir):
    ways = {}
    for way_id, summary in summary_by_way.items():
        milestones = {}
        for milestone_key in MILESTONE_ORDER:
            row = all_milestones[way_id][milestone_key]
            milestones[milestone_key] = None if row is None else {"version": int(row["version"]), "timestamp": json_ready(row["timestamp"]), "perimeter_m": json_ready(row["perimeter_m"]), "tags": row["tags"], "tag_changes": row["tag_changes"]}

        ways[str(way_id)] = {
            "metadata": way_meta(way_id),
            "summary": {key: json_ready(value) for key, value in summary.items()},
            "milestones": milestones,
        }

    return {
        "project_title": PROJECT_TITLE,
        "strike_date": iso_timestamp(strike_timestamp),
        "generated_at": iso_timestamp(pd.Timestamp.now(tz="UTC")),
        "ways": ways,
        "key_findings": key_findings,
        "conflation_assessment": conflation,
        "generated_files": sorted(repo_relative(path, root_dir) for path in generated_files),
    }


def write_results_txt(output_path, strike_timestamp, summary_df, milestone_df, combined_df, key_findings, summary_by_way, conflation):
    lines = [
        PROJECT_TITLE,
        "=" * 100,
        f"Strike divider: {format_timestamp(strike_timestamp)}",
        "",
        "KEY FINDINGS",
        "-" * 100,
    ]
    for finding in key_findings:
        lines.append(f"- {finding}")
    lines.extend(["", "CONFLATION RISK", "-" * 100, f"Overall rating: {conflation['overall_rating']}"])
    for indicator in conflation["indicators"]:
        lines.append(f"- {indicator['label']}: {indicator['detail']}")
    lines.extend(["", "WAY NARRATIVES", "-" * 100])
    for way_id in WAY_IDS:
        lines.append(f"{way_label(way_id)} ({way_id})")
        lines.append(textwrap.fill(build_way_narrative(summary_by_way[way_id], strike_timestamp), width=100))
        lines.append("")
    lines.extend(["MILESTONE COMPARISON", "-" * 100, milestone_df.to_string(index=False), "", "SUMMARY TABLE", "-" * 100, summary_df.to_string(index=False), "", "COMBINED EDIT TABLE", "-" * 100, combined_df[["way_id", "way_label", "version", "timestamp", "changeset", "user", "node_count", "closed_way", "perimeter_m", "edit_type", "tag_changes", "geometry_reconstruction"]].to_string(index=False), ""])
    output_path.write_text("\n".join(lines), encoding="utf-8")


def run():
    args = parse_args()
    strike_timestamp = parse_timestamp(args.strike_date)
    if pd.isna(strike_timestamp):
        raise SystemExit(f"Invalid --strike-date value: {args.strike_date}")

    root_dir = Path.cwd()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    print(f"Using strike divider: {format_timestamp(strike_timestamp)}")

    all_histories = {}
    all_node_ids = []
    for way_id in WAY_IDS:
        print(f"Fetching way history for {way_label(way_id)} ({way_id})...")
        versions = parse_way_history(fetch_way_history_xml(way_id))
        all_histories[way_id] = versions
        all_node_ids.extend(node_id for version in versions for node_id in version["node_refs"])

    node_histories = fetch_all_node_histories(all_node_ids)

    all_dfs = {}
    all_geometries = {}
    all_milestones = {}
    summary_rows = []
    combined_rows = []
    generated_files = []

    for way_id in WAY_IDS:
        df, geometries = build_way_dataframe(all_histories[way_id], node_histories)
        all_dfs[way_id] = df
        all_geometries[way_id] = geometries
        all_milestones[way_id] = get_milestones(df, strike_timestamp)
        summary_rows.append(summarise_way(df, all_milestones[way_id], strike_timestamp))
        combined_rows.append(df)

        csv_path = output_dir / f"way_{way_id}_history_analysis.csv"
        df.to_csv(csv_path, index=False)
        generated_files.append(csv_path)

        overlay_path = output_dir / f"way_{way_id}_geometry_overlay.png"
        plot_way_milestone_overlay(way_id, geometries, all_milestones[way_id], overlay_path)
        generated_files.append(overlay_path)

        timeline_path = output_dir / f"way_{way_id}_timeline.png"
        plot_way_timeline(df, way_id, strike_timestamp, timeline_path)
        generated_files.append(timeline_path)

    combined_df = pd.concat(combined_rows, ignore_index=True).sort_values(["timestamp", "way_id", "version"])
    summary_df = pd.DataFrame(summary_rows).sort_values("way_id").reset_index(drop=True)
    milestone_df = build_milestone_comparison_df(all_milestones)
    summary_by_way = {int(row["way_id"]): row for row in summary_rows}

    combined_csv = output_dir / "combined_way_history_analysis.csv"
    combined_df.to_csv(combined_csv, index=False)
    generated_files.append(combined_csv)

    summary_csv = output_dir / "way_history_summary.csv"
    summary_df.to_csv(summary_csv, index=False)
    generated_files.append(summary_csv)

    milestone_csv = output_dir / "milestone_state_comparison.csv"
    milestone_df.to_csv(milestone_csv, index=False)
    generated_files.append(milestone_csv)

    combined_timeline = output_dir / "combined_timeline.png"
    plot_combined_timeline(all_dfs, all_milestones, strike_timestamp, combined_timeline)
    generated_files.append(combined_timeline)

    perimeter_plot = output_dir / "perimeter_comparison.png"
    plot_perimeter_timeseries(all_dfs, strike_timestamp, perimeter_plot)
    generated_files.append(perimeter_plot)

    if not args.skip_animation:
        animation_path = output_dir / "way_history_animation.gif"
        animation_result = create_way_history_animation(all_histories, all_geometries, strike_timestamp, animation_path)
        if animation_result is not None:
            generated_files.append(animation_result)

    generated_files.extend(generate_state_maps(all_geometries, all_milestones, output_dir))

    conflation = build_conflation_assessment(all_milestones, all_geometries)
    conflation_plot = output_dir / "conflation_risk.png"
    plot_conflation_risk(conflation, conflation_plot)
    generated_files.append(conflation_plot)

    key_findings = generate_key_findings(summary_by_way, conflation, strike_timestamp)

    results_txt = output_dir / "results.txt"
    write_results_txt(results_txt, strike_timestamp, summary_df, milestone_df, combined_df, key_findings, summary_by_way, conflation)
    generated_files.append(results_txt)

    readme_path = generate_readme(root_dir, output_dir, strike_timestamp, summary_df, summary_by_way, key_findings, conflation, generated_files)
    generated_files.append(readme_path)

    summary_json = output_dir / "summary.json"
    generated_files.append(summary_json)
    summary_json.write_text(json.dumps(build_summary_json(strike_timestamp, summary_by_way, all_milestones, key_findings, conflation, generated_files, root_dir), ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== KEY FINDINGS ===")
    for finding in key_findings:
        print(f"- {finding}")
    print("\n=== SUMMARY TABLE ===")
    with pd.option_context("display.max_colwidth", 120, "display.width", 260):
        print(summary_df.to_string(index=False))
    print("\nSaved files:")
    for path in sorted(set(generated_files)):
        print(f"- {path}")
