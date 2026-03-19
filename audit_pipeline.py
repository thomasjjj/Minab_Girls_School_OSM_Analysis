import argparse
import io
import json
import math
import textwrap
import time
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

LOCAL_CONTEXT_RADII_M = [50, 100, 250, 500]
LOCAL_CONTEXT_QUERY_RADIUS_M = 500
LOCAL_CONTEXT_PRESTRIKE_OFFSET_DAYS = 7
OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_FALLBACK_API_URLS = [
    OVERPASS_API_URL,
    "https://overpass.kumi.systems/api/interpreter",
]
OVERPASS_MIN_INTERVAL_SECONDS = 6.0
BUILDING_AUDIT_WAY_ID = 942760673
BUILDING_AUDIT_TAG_FILTER = '["building"]'
BUILDING_ASSIGNMENT_ORDER = [
    "later_school_polygon",
    "later_compound_polygon",
    "latest_barracks_only",
    "outside_latest_barracks",
]
BUILDING_ASSIGNMENT_LABELS = {
    "later_school_polygon": "Later school polygon",
    "later_compound_polygon": "Later compound polygon",
    "latest_barracks_only": "Latest barracks-only area",
    "outside_latest_barracks": "Outside latest barracks",
}
BUILDING_ASSIGNMENT_COLORS = {
    "later_school_polygon": "#1976D2",
    "later_compound_polygon": "#00ACC1",
    "latest_barracks_only": "#FB8C00",
    "outside_latest_barracks": "#D32F2F",
}
BUILDING_SEMANTIC_ORDER = [
    "generic_building",
    "school_related",
    "military_related",
    "other_tagged_building",
]
BUILDING_SEMANTIC_LABELS = {
    "generic_building": "Generic building",
    "school_related": "School-related",
    "military_related": "Military-related",
    "other_tagged_building": "Other tagged building",
}
BUILDING_SEMANTIC_COLORS = {
    "generic_building": "#757575",
    "school_related": "#1976D2",
    "military_related": "#EF6C00",
    "other_tagged_building": "#8E24AA",
}

CDE_CATEGORIES = {
    "civilian_sensitive": {
        "label": "Civilian sensitive",
        "color": "#E53935",
        "match_tags": {
            "amenity": {"school", "kindergarten", "hospital", "clinic", "doctors",
                        "pharmacy", "place_of_worship", "community_centre",
                        "social_facility", "childcare", "nursing_home", "library"},
            "building": {"school", "hospital", "church", "mosque", "temple",
                         "kindergarten", "chapel"},
            "healthcare": None,
            "social_facility": None,
        },
    },
    "civilian_general": {
        "label": "Civilian general",
        "color": "#FB8C00",
        "match_tags": {
            "amenity": {"marketplace", "bank", "fuel", "restaurant", "cafe",
                        "parking", "post_office", "bus_station"},
            "shop": None,
            "building": {"residential", "apartments", "house", "commercial", "retail"},
            "landuse": {"residential", "commercial", "retail"},
            "tourism": None,
        },
    },
    "military_security": {
        "label": "Military / security",
        "color": "#1565C0",
        "match_tags": {
            "military": None,
            "landuse": {"military"},
            "building": {"military", "barracks"},
            "amenity": {"police"},
        },
    },
    "government_institutional": {
        "label": "Government / institutional",
        "color": "#7B1FA2",
        "match_tags": {
            "amenity": {"townhall", "courthouse", "prison", "fire_station"},
            "office": {"government"},
            "building": {"government", "public"},
            "government": None,
        },
    },
    "infrastructure_access": {
        "label": "Infrastructure / access",
        "color": "#00897B",
        "match_tags": {
            "highway": {"primary", "secondary", "tertiary", "residential",
                        "service", "track"},
            "power": None,
            "man_made": {"water_tower", "tower", "mast"},
            "barrier": {"wall", "fence", "gate"},
        },
    },
}
CDE_CATEGORY_ORDER = [
    "civilian_sensitive", "civilian_general", "military_security",
    "government_institutional", "infrastructure_access", "unknown",
]
CDE_CATEGORY_COLORS = {cat: info["color"] for cat, info in CDE_CATEGORIES.items()}
CDE_CATEGORY_COLORS["unknown"] = "#9E9E9E"
CDE_CATEGORY_LABELS = {cat: info["label"] for cat, info in CDE_CATEGORIES.items()}
CDE_CATEGORY_LABELS["unknown"] = "Unknown / unclassified"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "IranGirlsSchoolHistoryAudit/2.0"})
TILE_CACHE = {}
LAST_OVERPASS_REQUEST_TS = None


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


def format_date(value):
    if value is None or pd.isna(value):
        return "n/a"
    return pd.Timestamp(value).tz_convert("UTC").strftime("%Y-%m-%d")


def iso_timestamp(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).tz_convert("UTC").isoformat()


def local_context_prestrike_descriptor():
    if LOCAL_CONTEXT_PRESTRIKE_OFFSET_DAYS == 7:
        return "one week before strike"
    if LOCAL_CONTEXT_PRESTRIKE_OFFSET_DAYS == 1:
        return "one day before strike"
    return f"{LOCAL_CONTEXT_PRESTRIKE_OFFSET_DAYS} days before strike"


def local_context_prestrike_timestamp(strike_timestamp):
    return pd.Timestamp(strike_timestamp).tz_convert("UTC") - pd.Timedelta(days=LOCAL_CONTEXT_PRESTRIKE_OFFSET_DAYS)


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


def point_in_polygon_xy(point, polygon_xy):
    """Ray-casting algorithm to check if a point lies inside a polygon (2D XY)."""
    x, y = point
    n = len(polygon_xy)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon_xy[i]
        xj, yj = polygon_xy[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def polygon_contains_polygon(outer_coords, inner_coords):
    """Check what fraction of inner polygon vertices lie inside the outer polygon."""
    if not outer_coords or not inner_coords or len(outer_coords) < 3:
        return None
    all_points = outer_coords + inner_coords
    reference = (
        sum(p[0] for p in all_points) / len(all_points),
        sum(p[1] for p in all_points) / len(all_points),
    )
    outer_xy = latlon_to_local_xy(close_coords(outer_coords), reference)
    inner_xy = latlon_to_local_xy(inner_coords, reference)
    inside_count = sum(1 for pt in inner_xy if point_in_polygon_xy(pt, outer_xy))
    return inside_count / len(inner_xy) if inner_xy else None


def point_in_polygon(point, polygon_coords):
    """Check whether a latitude/longitude point falls inside a polygon."""
    if point is None or not polygon_coords or len(polygon_coords) < 3:
        return False
    all_points = list(polygon_coords) + [point]
    reference = (
        sum(lat for lat, _ in all_points) / len(all_points),
        sum(lon for _, lon in all_points) / len(all_points),
    )
    polygon_xy = latlon_to_local_xy(close_coords(polygon_coords), reference)
    point_xy = latlon_to_local_xy([point], reference)[0]
    return point_in_polygon_xy(point_xy, polygon_xy)


def feature_name(tags):
    return tags.get("name") or tags.get("name:en") or tags.get("name:fa") or ""


def classify_building_semantics(tags):
    if not tags:
        return "other_tagged_building"

    building_value = (tags.get("building") or "").strip().lower()
    amenity_value = (tags.get("amenity") or "").strip().lower()
    education_value = (tags.get("education") or "").strip().lower()
    landuse_value = (tags.get("landuse") or "").strip().lower()
    military_value = (tags.get("military") or "").strip().lower()

    if building_value == "school" or amenity_value == "school" or education_value == "school":
        return "school_related"
    if building_value in {"military", "barracks"} or military_value or landuse_value == "military":
        return "military_related"
    if building_value in {"", "yes"}:
        return "generic_building"
    return "other_tagged_building"


def classify_building_assignment(point, reference_polygons):
    school_coords = reference_polygons.get("school", [])
    compound_coords = reference_polygons.get("compound", [])
    barracks_coords = reference_polygons.get("barracks", [])

    if point_in_polygon(point, school_coords):
        return "later_school_polygon"
    if point_in_polygon(point, compound_coords):
        return "later_compound_polygon"
    if point_in_polygon(point, barracks_coords):
        return "latest_barracks_only"
    return "outside_latest_barracks"


def find_shared_nodes(node_refs_a, node_refs_b):
    """Return the set of node IDs shared between two way versions."""
    return set(node_refs_a) & set(node_refs_b)


def analyze_changeset_patterns(all_histories):
    """Identify changesets that edited multiple ways simultaneously."""
    changeset_edits = {}
    for way_id, versions in all_histories.items():
        for version in versions:
            cs = version.get("changeset")
            if cs is None:
                continue
            changeset_edits.setdefault(cs, []).append({
                "way_id": way_id,
                "version": version["version"],
                "timestamp": version["timestamp"],
                "user": version["user"],
            })
    multi_way_changesets = {
        cs: edits for cs, edits in changeset_edits.items()
        if len({e["way_id"] for e in edits}) > 1
    }
    return multi_way_changesets


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
    area_series = df["area_sq_m"].dropna()
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
        "first_area_sq_m": safe_float(area_series.iloc[0]) if not area_series.empty else None,
        "latest_area_sq_m": safe_float(area_series.iloc[-1]) if not area_series.empty else None,
    }

    for milestone_key in MILESTONE_ORDER:
        row = milestones[milestone_key]
        summary[f"{milestone_key}_version"] = int(row["version"]) if row is not None else None
        summary[f"{milestone_key}_timestamp"] = row["timestamp"] if row is not None else pd.NaT
        summary[f"{milestone_key}_perimeter_m"] = safe_float(row["perimeter_m"]) if row is not None else None
        summary[f"{milestone_key}_area_sq_m"] = safe_float(row["area_sq_m"]) if row is not None and pd.notna(row.get("area_sq_m")) else None
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
            "area_sq_m": f"{row['area_sq_m']:.0f}" if pd.notna(row.get("area_sq_m")) else "n/a",
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
        if "\n" in subtitle:
            wrapped_subtitle = "\n".join(textwrap.fill(line, width=62) for line in subtitle.splitlines())
        else:
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


def build_combined_state_subtitle(milestone_key, all_milestones, strike_timestamp):
    lines = [f"Strike date: {format_date(strike_timestamp)}"]

    if milestone_key == "last_pre_strike":
        lines.append("State shown: latest mapped state before the strike")
    elif milestone_key == "first_post_strike":
        lines.append("State shown: first mapped state after the strike")
    else:
        lines.append(f"State shown: {MILESTONE_LABELS[milestone_key]}")

    for way_id in WAY_IDS:
        row = all_milestones[way_id][milestone_key]
        if row is None:
            if milestone_key == "last_pre_strike":
                lines.append(f"{way_short_label(way_id)}: not mapped before strike")
            elif milestone_key == "first_post_strike":
                lines.append(f"{way_short_label(way_id)}: no post-strike state")
            else:
                lines.append(f"{way_short_label(way_id)}: no state available")
            continue

        lines.append(
            f"{way_short_label(way_id)}: v{int(row['version'])} on {format_date(row['timestamp'])}"
        )

    return "\n".join(lines)


def generate_state_maps(all_geometries, all_milestones, strike_timestamp, output_dir):
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
        if milestone_key in {"last_pre_strike", "first_post_strike"}:
            subtitle = build_combined_state_subtitle(milestone_key, all_milestones, strike_timestamp)
        else:
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


def create_before_after_comparison(all_geometries, all_milestones, strike_timestamp, output_path):
    """Create a side-by-side figure comparing last pre-strike and first post-strike states."""
    pre_selection = {}
    post_selection = {}
    pre_dates = {}
    post_dates = {}
    for way_id in WAY_IDS:
        pre_row = all_milestones[way_id]["last_pre_strike"]
        post_row = all_milestones[way_id]["first_post_strike"]
        if pre_row is not None:
            coords = all_geometries[way_id].get(int(pre_row["version"]), [])
            if len(coords) >= 2:
                pre_selection[way_id] = coords
            pre_dates[way_id] = f"v{int(pre_row['version'])} ({format_date(pre_row['timestamp'])})"
        else:
            pre_dates[way_id] = "not mapped"
        if post_row is not None:
            coords = all_geometries[way_id].get(int(post_row["version"]), [])
            if len(coords) >= 2:
                post_selection[way_id] = coords
            post_dates[way_id] = f"v{int(post_row['version'])} ({format_date(post_row['timestamp'])})"
        else:
            post_dates[way_id] = "no post-strike state"

    all_coords = []
    for coords in list(pre_selection.values()) + list(post_selection.values()):
        all_coords.extend(coords)
    if not all_coords:
        save_note_figure(output_path, "Before / after strike comparison", "No geometry available for comparison.")
        return

    bounds = expand_bounds(all_coords, padding_ratio=0.18)
    basemap_image, basemap_info = build_basemap(bounds)

    fig, (ax_pre, ax_post) = plt.subplots(1, 2, figsize=(16, 9.5))

    panel_configs = [
        (ax_pre, pre_selection, pre_dates, "BEFORE strike (last pre-strike state)", True),
        (ax_post, post_selection, post_dates, "AFTER strike (first post-strike state)", False),
    ]
    for ax, selection, dates, title, is_before in panel_configs:
        ax.imshow(basemap_image, origin="upper")
        legend_handles = []
        for way_id, coords in sorted(selection.items()):
            plot_coords = close_coords(coords) if len(coords) >= 3 else coords
            points = geometry_to_pixel_points(plot_coords, basemap_info)
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            color = way_color_hex(way_id)
            if len(coords) >= 3:
                ax.fill(xs, ys, color=color, alpha=0.16)
            ax.plot(xs, ys, color=color, linewidth=3, alpha=0.95)
            centroid = geometry_centroid(coords)
            if centroid is not None:
                x_px, y_px = latlon_to_basemap_pixels(centroid[0], centroid[1], basemap_info)
                ax.text(x_px, y_px, way_annotation_label(way_id), fontsize=8, ha="center", va="center",
                        bbox={"boxstyle": "round,pad=0.22", "facecolor": "white", "edgecolor": color, "alpha": 0.92})
            legend_handles.append(Line2D([0], [0], color=color, linewidth=3, label=way_display_label(way_id)))

        if not selection:
            ax.text(0.5, 0.5, "No ways mapped at this state", fontsize=14, ha="center", va="center",
                    transform=ax.transAxes, color="#888888",
                    bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.9})

        ax.set_title(title, fontsize=11, fontweight="bold", color="#C62828" if is_before else "#1565C0")
        ax.set_xlim(0, basemap_info["width_px"])
        ax.set_ylim(basemap_info["height_px"], 0)
        ax.set_axis_off()
        if legend_handles:
            ax.legend(handles=legend_handles, loc="upper right", framealpha=0.95, fontsize=8)

        # Date info box showing state dates per way
        date_lines = [f"Strike date: {format_date(strike_timestamp)}"]
        for way_id in WAY_IDS:
            date_lines.append(f"{way_short_label(way_id)}: {dates[way_id]}")
        ax.text(0.01, 0.99, "\n".join(date_lines), transform=ax.transAxes, fontsize=8.5,
                ha="left", va="top",
                bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "alpha": 0.92, "edgecolor": "#888888"})

    ways_before = len(pre_selection)
    ways_after = len(post_selection)
    fig.suptitle(f"Pre-strike vs post-strike OSM state  |  Strike date: {format_date(strike_timestamp)}  |  {ways_before} way(s) before, {ways_after} after",
                 fontsize=12, fontweight="bold", y=0.98)
    fig.text(0.01, 0.01, "Basemap: OpenStreetMap", fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def plot_way_timeline(df, way_id, strike_timestamp, output_path):
    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.plot(df["timestamp"], df["version"], color=way_color_hex(way_id), linewidth=1.5, alpha=0.35)

    for edit_type, style in EDIT_TYPE_STYLES.items():
        subset = df[df["edit_type"] == edit_type]
        if subset.empty:
            continue
        ax.scatter(subset["timestamp"], subset["version"], marker=style["marker"], s=70, color=style["color"], edgecolors="white", linewidths=0.7, label=edit_type, zorder=3)

    milestones = get_milestones(df, strike_timestamp)
    version_labels = {}
    for milestone_key in MILESTONE_ORDER:
        row = milestones[milestone_key]
        if row is None:
            continue
        v = int(row["version"])
        if v in version_labels:
            version_labels[v]["text"] += " / " + MILESTONE_LABELS[milestone_key]
        else:
            version_labels[v] = {"text": MILESTONE_LABELS[milestone_key], "row": row}
    for entry in version_labels.values():
        ax.annotate(entry["text"], xy=(entry["row"]["timestamp"], entry["row"]["version"]), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8, bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "#999999", "alpha": 0.9})

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


def _draw_combined_timeline_on_ax(ax, all_dfs, all_milestones, strike_timestamp, show_labels=True, label_fontsize=7):
    y_positions = {way_id: index for index, way_id in enumerate(WAY_IDS)}

    for way_id in WAY_IDS:
        df = all_dfs[way_id]
        y_value = y_positions[way_id]
        for edit_type, style in EDIT_TYPE_STYLES.items():
            subset = df[df["edit_type"] == edit_type]
            if subset.empty:
                continue
            ax.scatter(subset["timestamp"], [y_value] * len(subset), marker=style["marker"], color=way_color_hex(way_id), edgecolors="white", linewidths=0.7, s=90, alpha=0.95)

        if show_labels:
            # Group milestones that share the same version to avoid overlapping labels
            version_labels = {}
            for milestone_key in MILESTONE_ORDER:
                row = all_milestones[way_id][milestone_key]
                if row is None:
                    continue
                v = int(row["version"])
                if v in version_labels:
                    version_labels[v]["text"] += " / " + MILESTONE_LABELS[milestone_key]
                else:
                    version_labels[v] = {"text": MILESTONE_LABELS[milestone_key], "row": row}
            for entry in version_labels.values():
                ax.annotate(entry["text"], xy=(entry["row"]["timestamp"], y_value), xytext=(0, 11), textcoords="offset points", ha="center", fontsize=label_fontsize, bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "edgecolor": "#BBBBBB", "alpha": 0.9})

    ax.axvline(strike_timestamp, color="#D32F2F", linestyle="--", linewidth=1.6)
    ax.set_yticks([y_positions[way_id] for way_id in WAY_IDS], [way_display_label(way_id) for way_id in WAY_IDS])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    ax.grid(True, axis="x", linestyle=":", alpha=0.4)


def plot_combined_timeline(all_dfs, all_milestones, strike_timestamp, output_path):
    all_timestamps = pd.concat([df["timestamp"] for df in all_dfs.values()])
    post_strike = all_timestamps[all_timestamps >= strike_timestamp]
    has_pre_strike = (all_timestamps < strike_timestamp).any()
    has_post_detail = len(post_strike) > 2

    if has_pre_strike and has_post_detail:
        fig, (ax_full, ax_zoom) = plt.subplots(1, 2, figsize=(16, 5.2), width_ratios=[1, 2])
        _draw_combined_timeline_on_ax(ax_full, all_dfs, all_milestones, strike_timestamp, show_labels=False)
        ax_full.set_title("Full history (overview)", fontsize=10)
        ax_full.set_xlabel("Timestamp (UTC)")
        for label in ax_full.get_xticklabels():
            label.set_rotation(20)
            label.set_ha("right")

        _draw_combined_timeline_on_ax(ax_zoom, all_dfs, all_milestones, strike_timestamp, show_labels=True, label_fontsize=7.5)
        zoom_start = strike_timestamp - pd.Timedelta(days=3)
        zoom_end = post_strike.max() + pd.Timedelta(days=1)
        ax_zoom.set_xlim(zoom_start, zoom_end)
        ax_zoom.set_title("Post-strike detail (zoomed)", fontsize=10)
        ax_zoom.set_xlabel("Timestamp (UTC)")
        for label in ax_zoom.get_xticklabels():
            label.set_rotation(20)
            label.set_ha("right")

        way_handles = [Line2D([0], [0], marker="o", color="none", markerfacecolor=way_color_hex(way_id), markersize=9, label=way_display_label(way_id)) for way_id in WAY_IDS]
        type_handles = [Line2D([0], [0], marker=style["marker"], color="none", markerfacecolor=style["color"], markersize=8, label=edit_type) for edit_type, style in EDIT_TYPE_STYLES.items()]
        ax_zoom.legend(handles=way_handles + type_handles, fontsize=7.5, ncol=2, loc="upper left", framealpha=0.95)
    else:
        fig, ax = plt.subplots(figsize=(12, 4.6))
        _draw_combined_timeline_on_ax(ax, all_dfs, all_milestones, strike_timestamp, show_labels=True)
        ax.set_title("Combined edit timeline by way")
        ax.set_xlabel("Timestamp (UTC)")
        way_handles = [Line2D([0], [0], marker="o", color="none", markerfacecolor=way_color_hex(way_id), markersize=9, label=way_display_label(way_id)) for way_id in WAY_IDS]
        type_handles = [Line2D([0], [0], marker=style["marker"], color="none", markerfacecolor=style["color"], markersize=8, label=edit_type) for edit_type, style in EDIT_TYPE_STYLES.items()]
        ax.legend(handles=way_handles + type_handles, fontsize=8, ncol=2, loc="upper left", framealpha=0.95)
        plt.xticks(rotation=20, ha="right")

    fig.suptitle("Combined edit timeline by way", fontsize=13, fontweight="bold", y=1.01)
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def plot_perimeter_timeseries(all_dfs, strike_timestamp, output_path):
    fig, ax = plt.subplots(figsize=(11, 5))
    gap_threshold = pd.Timedelta(days=90)

    for way_id in WAY_IDS:
        series = all_dfs[way_id].dropna(subset=["perimeter_m"]).sort_values("timestamp")
        if series.empty:
            continue
        timestamps = series["timestamp"].values
        perimeters = series["perimeter_m"].values
        color = way_color_hex(way_id)

        # Split into segments where gaps > threshold to avoid misleading interpolation
        segment_start = 0
        for i in range(1, len(timestamps)):
            gap = pd.Timestamp(timestamps[i]) - pd.Timestamp(timestamps[i - 1])
            if gap > gap_threshold:
                seg_t = timestamps[segment_start:i]
                seg_p = perimeters[segment_start:i]
                ax.plot(seg_t, seg_p, marker="o", linewidth=2, markersize=5, color=color)
                # Draw dashed line across the gap to show discontinuity
                ax.plot([timestamps[i - 1], timestamps[i]], [perimeters[i - 1], perimeters[i]],
                        linewidth=1, linestyle=":", color=color, alpha=0.4)
                segment_start = i
        seg_t = timestamps[segment_start:]
        seg_p = perimeters[segment_start:]
        ax.plot(seg_t, seg_p, marker="o", linewidth=2, markersize=5, color=color, label=way_display_label(way_id))

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


def build_conflation_assessment(all_milestones, all_geometries, all_histories=None):
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

    # Spatial containment analysis: would the school location have fallen inside the pre-strike barracks?
    school_in_barracks_pre = None
    if barracks_pre is not None and latest_school_coords:
        barracks_pre_coords = all_geometries[barracks_id].get(int(barracks_pre["version"]), [])
        if barracks_pre_coords and len(barracks_pre_coords) >= 3:
            school_in_barracks_pre = polygon_contains_polygon(barracks_pre_coords, latest_school_coords)
            if school_in_barracks_pre is not None and school_in_barracks_pre > 0.5:
                pct = int(school_in_barracks_pre * 100)
                indicators.append({"label": "School location inside pre-strike barracks", "status": "risk", "detail": f"{pct}% of the latest school polygon vertices fall inside the pre-strike barracks boundary, meaning the school area was subsumed within the military perimeter before the strike."})
                score += 10
            elif school_in_barracks_pre is not None:
                pct = int(school_in_barracks_pre * 100)
                indicators.append({"label": "School location relative to pre-strike barracks", "status": "ambiguous", "detail": f"{pct}% of the latest school polygon vertices fall inside the pre-strike barracks boundary."})

    # Shared-node analysis: do the post-strike ways share boundary nodes?
    shared_nodes_info = {}
    if all_histories is not None:
        latest_refs = {}
        for way_id in [school_id, compound_id, barracks_id]:
            versions = all_histories.get(way_id, [])
            if versions:
                latest_refs[way_id] = versions[-1]["node_refs"]
        if school_id in latest_refs and compound_id in latest_refs:
            shared = find_shared_nodes(latest_refs[school_id], latest_refs[compound_id])
            shared_nodes_info["school_compound"] = len(shared)
            if shared:
                indicators.append({"label": "Shared boundary nodes (school-compound)", "status": "risk", "detail": f"The school and compound polygons share {len(shared)} boundary node(s), confirming they were explicitly drawn as adjacent with touching edges."})
        if school_id in latest_refs and barracks_id in latest_refs:
            shared = find_shared_nodes(latest_refs[school_id], latest_refs[barracks_id])
            shared_nodes_info["school_barracks"] = len(shared)
            if shared:
                indicators.append({"label": "Shared boundary nodes (school-barracks)", "status": "ambiguous", "detail": f"The school and barracks polygons share {len(shared)} boundary node(s)."})

    # Area metrics
    school_area = polygon_area_sq_m(latest_school_coords) if latest_school_coords and len(latest_school_coords) >= 3 else None
    barracks_pre_area = None
    if barracks_pre is not None:
        barracks_pre_coords = all_geometries[barracks_id].get(int(barracks_pre["version"]), [])
        if barracks_pre_coords and len(barracks_pre_coords) >= 3:
            barracks_pre_area = polygon_area_sq_m(barracks_pre_coords)

    score = max(0, min(100, score))
    rating = "High" if score >= 65 else "Moderate" if score >= 40 else "Low"

    return {
        "score": score,
        "overall_rating": rating,
        "indicators": indicators,
        "latest_school_compound_distance_m": safe_float(school_compound_distance),
        "latest_school_barracks_distance_m": safe_float(school_barracks_distance),
        "school_in_barracks_pre_fraction": safe_float(school_in_barracks_pre),
        "shared_nodes": shared_nodes_info,
        "school_area_sq_m": safe_float(school_area),
        "barracks_pre_area_sq_m": safe_float(barracks_pre_area),
    }


def plot_conflation_risk(conflation, output_path):
    num_indicators = len(conflation["indicators"])
    fig_height = max(7.5, 4.0 + num_indicators * 1.0)
    fig, ax = plt.subplots(figsize=(12, fig_height))
    ax.axis("off")

    rating_color = {"Low": "#2E7D32", "Moderate": "#F9A825", "High": "#C62828"}[conflation["overall_rating"]]
    box_top = 0.92
    box_height = 0.18
    rating_box = FancyBboxPatch((0.03, box_top - box_height), 0.28, box_height, boxstyle="round,pad=0.02,rounding_size=0.02", facecolor=rating_color, edgecolor="#333333", linewidth=1.2, transform=ax.transAxes)
    ax.add_patch(rating_box)
    ax.text(0.17, box_top - 0.04, "Conflation Risk", fontsize=15, color="white", fontweight="bold", ha="center", transform=ax.transAxes)
    ax.text(0.17, box_top - 0.10, conflation["overall_rating"], fontsize=26, color="white", fontweight="bold", ha="center", transform=ax.transAxes)
    ax.text(0.17, box_top - 0.16, "Qualitative audit rating", fontsize=11, color="white", ha="center", transform=ax.transAxes)

    status_colors = {"clear": "#2E7D32", "ambiguous": "#F9A825", "risk": "#C62828", "unknown": "#607D8B"}
    status_symbols = {"clear": "CLEAR", "ambiguous": "MIXED", "risk": "RISK", "unknown": "N/A"}
    ax.text(0.36, 0.93, "Traffic-light indicators of pre-strike map clarity", fontsize=12, fontweight="bold", ha="left", transform=ax.transAxes)

    indicator_step = min(0.11, 0.80 / max(num_indicators, 1))
    y = 0.86
    for indicator in conflation["indicators"]:
        color = status_colors[indicator["status"]]
        ax.scatter([0.37], [y], s=160, color=color, transform=ax.transAxes, zorder=5)
        ax.text(0.37, y, status_symbols[indicator["status"]], fontsize=5.5, color="white", fontweight="bold", ha="center", va="center", transform=ax.transAxes, zorder=6)
        ax.text(0.40, y + 0.015, indicator["label"], fontsize=10.5, fontweight="bold", ha="left", va="top", transform=ax.transAxes)
        ax.text(0.40, y - 0.012, textwrap.fill(indicator["detail"], 72), fontsize=9, ha="left", va="top", transform=ax.transAxes, color="#333333")
        y -= indicator_step

    # Spatial metrics footer
    footer_lines = []
    if conflation["latest_school_compound_distance_m"] is not None:
        d = conflation["latest_school_compound_distance_m"]
        footer_lines.append(f"School-to-compound vertex distance: {d:.1f} m" + (" (shared boundary)" if d < 1.0 else ""))
    if conflation["latest_school_barracks_distance_m"] is not None:
        d = conflation["latest_school_barracks_distance_m"]
        footer_lines.append(f"School-to-barracks vertex distance: {d:.1f} m" + (" (shared boundary)" if d < 1.0 else ""))
    if conflation.get("school_area_sq_m") is not None:
        footer_lines.append(f"School polygon area: {conflation['school_area_sq_m']:,.0f} sq m")
    if conflation.get("barracks_pre_area_sq_m") is not None:
        footer_lines.append(f"Pre-strike barracks area: {conflation['barracks_pre_area_sq_m']:,.0f} sq m")
    frac = conflation.get("school_in_barracks_pre_fraction")
    if frac is not None:
        footer_lines.append(f"School vertices inside pre-strike barracks: {int(frac * 100)}%")
    for key, count in conflation.get("shared_nodes", {}).items():
        footer_lines.append(f"Shared boundary nodes ({key.replace('_', '-')}): {count}")
    if footer_lines:
        ax.text(0.03, max(0.02, y - 0.04), "\n".join(footer_lines), fontsize=9.5, ha="left", va="top", transform=ax.transAxes, family="monospace",
                bbox={"boxstyle": "round,pad=0.3", "facecolor": "#F5F5F5", "edgecolor": "#CCCCCC", "alpha": 0.95})

    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def compute_all_geometries_centroid(all_geometries, all_milestones):
    """Compute the centroid of all analysed ways' latest geometries."""
    all_points = []
    for way_id in WAY_IDS:
        latest = all_milestones[way_id]["latest"]
        if latest is not None:
            coords = all_geometries[way_id].get(int(latest["version"]), [])
            all_points.extend(coords)
    if not all_points:
        return None, None
    lat = sum(p[0] for p in all_points) / len(all_points)
    lon = sum(p[1] for p in all_points) / len(all_points)
    return lat, lon


def build_overpass_query(center_lat, center_lon, radius_m, date_iso=None):
    """Construct an Overpass QL query for features within radius of center."""
    date_clause = f'[date:"{date_iso}"]' if date_iso else ""
    return (
        f"[out:json][timeout:90]{date_clause};\n"
        f"(\n"
        f"  nwr(around:{radius_m},{center_lat},{center_lon});\n"
        f");\n"
        f"out center tags;\n"
    )


def build_overpass_bbox_query(bounds, date_iso=None, tag_filter=""):
    """Construct an Overpass QL query for features in a bounding box."""
    min_lat, max_lat, min_lon, max_lon = bounds
    date_clause = f'[date:"{date_iso}"]' if date_iso else ""
    return (
        f"[out:json][timeout:90]{date_clause};\n"
        f"(\n"
        f"  nwr{tag_filter}({min_lat:.7f},{min_lon:.7f},{max_lat:.7f},{max_lon:.7f});\n"
        f");\n"
        f"out center tags;\n"
    )


def fetch_overpass(query_string):
    """POST query to Overpass API with retry logic."""
    global LAST_OVERPASS_REQUEST_TS
    last_errors = []
    for attempt in range(3):
        for endpoint in OVERPASS_FALLBACK_API_URLS:
            try:
                if LAST_OVERPASS_REQUEST_TS is not None:
                    elapsed = time.monotonic() - LAST_OVERPASS_REQUEST_TS
                    if elapsed < OVERPASS_MIN_INTERVAL_SECONDS:
                        wait_time = OVERPASS_MIN_INTERVAL_SECONDS - elapsed
                        print(f"Waiting {wait_time:.1f}s before Overpass request...")
                        time.sleep(wait_time)
                response = SESSION.post(
                    endpoint,
                    data={"data": query_string},
                    timeout=120,
                )
                LAST_OVERPASS_REQUEST_TS = time.monotonic()
                response.raise_for_status()
                if endpoint != OVERPASS_API_URL:
                    print(f"Using fallback Overpass endpoint: {endpoint}")
                return response.json()
            except requests.RequestException as exc:
                LAST_OVERPASS_REQUEST_TS = time.monotonic()
                last_errors.append(f"{endpoint}: {exc}")
                if endpoint != OVERPASS_FALLBACK_API_URLS[-1]:
                    print(f"Overpass endpoint {endpoint} failed ({exc}), trying fallback mirror...")
                    continue

        if attempt < 2:
            wait = 10 * (attempt + 1)
            print(f"Overpass query attempt {attempt + 1} failed across all endpoints, retrying in {wait}s...")
            time.sleep(wait)
        else:
            error_text = " | ".join(last_errors[-len(OVERPASS_FALLBACK_API_URLS):]) if last_errors else "unknown error"
            print(f"Warning: Overpass query failed after 3 attempts ({error_text}). Continuing with empty results.")
            return {"elements": [], "_fetch_failed": True, "_error": error_text}


def classify_osm_element(tags):
    """Classify an OSM element into a CDE-relevant category based on its tags."""
    if not tags:
        return "unknown"
    for category, info in CDE_CATEGORIES.items():
        for tag_key, accepted_values in info["match_tags"].items():
            if tag_key in tags:
                if accepted_values is None:
                    return category
                if tags[tag_key] in accepted_values:
                    return category
    return "unknown"


def extract_nearby_features(overpass_response, center_lat, center_lon, radii):
    """Parse Overpass response into a list of classified feature dicts."""
    elements = overpass_response.get("elements", [])
    reference = (center_lat, center_lon)
    features = []

    for elem in elements:
        elem_type = elem.get("type", "")
        osm_id = elem.get("id")
        tags = elem.get("tags", {})

        # Get coordinates
        if elem_type == "node":
            lat = elem.get("lat")
            lon = elem.get("lon")
        else:
            center = elem.get("center", {})
            lat = center.get("lat")
            lon = center.get("lon")

        if lat is None or lon is None:
            continue

        # Compute distance from analysis centroid
        xy = latlon_to_local_xy([(lat, lon)], reference)
        distance_m = math.hypot(xy[0][0], xy[0][1])

        category = classify_osm_element(tags)
        name = tags.get("name") or tags.get("name:en") or tags.get("name:fa") or ""

        # Determine radius bands
        radius_bands = {}
        for r in radii:
            radius_bands[f"within_{r}m"] = distance_m <= r

        feature = {
            "element_type": elem_type,
            "osm_id": osm_id,
            "name": name,
            "lat": lat,
            "lon": lon,
            "distance_m": round(distance_m, 1),
            "cde_category": category,
            "cde_label": CDE_CATEGORY_LABELS.get(category, "Unknown"),
            "tags_str": "; ".join(f"{k}={v}" for k, v in sorted(tags.items())),
        }
        feature.update(radius_bands)
        features.append(feature)

    return features


def build_nearby_features_df(features):
    """Convert features list to DataFrame."""
    if not features:
        return pd.DataFrame()
    return pd.DataFrame(features)


def compare_prestrike_current(df_pre, df_current):
    """Compare pre-strike and current nearby feature sets."""
    if df_pre.empty and df_current.empty:
        return pd.DataFrame()

    merge_cols = ["element_type", "osm_id"]

    if df_pre.empty:
        result = df_current[["element_type", "osm_id", "name", "distance_m", "cde_category", "cde_label", "tags_str"]].copy()
        result["in_prestrike"] = False
        result["in_current"] = True
        result["status"] = "added_after_strike"
        return result

    if df_current.empty:
        result = df_pre[["element_type", "osm_id", "name", "distance_m", "cde_category", "cde_label", "tags_str"]].copy()
        result["in_prestrike"] = True
        result["in_current"] = False
        result["status"] = "removed_after_strike"
        return result

    pre_subset = df_pre[["element_type", "osm_id", "name", "distance_m", "cde_category", "cde_label", "tags_str"]].copy()
    pre_subset = pre_subset.rename(columns={"name": "name_pre", "cde_category": "cde_category_pre",
                                             "cde_label": "cde_label_pre", "tags_str": "tags_pre",
                                             "distance_m": "distance_m_pre"})

    cur_subset = df_current[["element_type", "osm_id", "name", "distance_m", "cde_category", "cde_label", "tags_str"]].copy()
    cur_subset = cur_subset.rename(columns={"name": "name_current", "cde_category": "cde_category_current",
                                             "cde_label": "cde_label_current", "tags_str": "tags_current",
                                             "distance_m": "distance_m_current"})

    merged = pd.merge(pre_subset, cur_subset, on=merge_cols, how="outer", indicator=True)
    merged["in_prestrike"] = merged["_merge"].isin(["left_only", "both"])
    merged["in_current"] = merged["_merge"].isin(["right_only", "both"])
    merged["status"] = merged["_merge"].map({
        "left_only": "removed_after_strike",
        "right_only": "added_after_strike",
        "both": "present_both",
    })
    merged.drop(columns=["_merge"], inplace=True)
    return merged


def build_local_context_summary(df_pre, df_current, radii, center_lat, center_lon,
                                pre_date_iso, strike_date_iso, pre_date_description,
                                pre_date_offset_days):
    """Build summary dict for local context JSON output."""
    def counts_by_category(df):
        if df.empty:
            return {cat: 0 for cat in CDE_CATEGORY_ORDER}
        counts = df["cde_category"].value_counts().to_dict()
        return {cat: counts.get(cat, 0) for cat in CDE_CATEGORY_ORDER}

    def counts_by_radius(df, radii):
        result = {}
        for r in radii:
            col = f"within_{r}m"
            result[str(r)] = int(df[col].sum()) if col in df.columns else 0
        return result

    def named_features(df):
        if df.empty:
            return []
        named = df[df["name"].astype(str).str.len() > 0].sort_values("distance_m")
        return [
            {"name": row["name"], "category": row["cde_category"], "distance_m": row["distance_m"]}
            for _, row in named.head(30).iterrows()
        ]

    comparison_counts = {"added": 0, "removed": 0, "unchanged": 0}
    if not df_pre.empty or not df_current.empty:
        comp = compare_prestrike_current(df_pre, df_current)
        if not comp.empty:
            comparison_counts["added"] = int((comp["status"] == "added_after_strike").sum())
            comparison_counts["removed"] = int((comp["status"] == "removed_after_strike").sum())
            comparison_counts["unchanged"] = int((comp["status"] == "present_both").sum())

    return {
        "center": {"lat": center_lat, "lon": center_lon},
        "strike_date": strike_date_iso,
        "query_date_prestrike": pre_date_iso,
        "query_description_prestrike": pre_date_description,
        "query_offset_days_prestrike": pre_date_offset_days,
        "query_date_current": "now",
        "radii_m": radii,
        "prestrike": {
            "total_features": len(df_pre),
            "by_category": counts_by_category(df_pre),
            "by_radius": counts_by_radius(df_pre, radii),
            "named_features": named_features(df_pre),
        },
        "current": {
            "total_features": len(df_current),
            "by_category": counts_by_category(df_current),
            "by_radius": counts_by_radius(df_current, radii),
            "named_features": named_features(df_current),
        },
        "comparison": comparison_counts,
    }


def draw_radius_circles(ax, center_lat, center_lon, radii, basemap_info):
    """Draw concentric radius circles on a basemap axes."""
    cx, cy = latlon_to_basemap_pixels(center_lat, center_lon, basemap_info)
    for r_m in radii:
        # Approximate pixel radius: offset center by r_m meters north and measure pixel distance
        delta_lat = r_m / 111_320.0
        _, ny = latlon_to_basemap_pixels(center_lat + delta_lat, center_lon, basemap_info)
        pixel_radius = abs(cy - ny)
        circle = plt.Circle((cx, cy), pixel_radius, fill=False, edgecolor="#666666",
                             linestyle="--", linewidth=1.0, alpha=0.6)
        ax.add_patch(circle)
        ax.text(cx + pixel_radius * 0.71, cy - pixel_radius * 0.71, f"{r_m}m",
                fontsize=7, color="#555555", ha="left", va="bottom",
                bbox={"boxstyle": "round,pad=0.1", "facecolor": "white", "alpha": 0.7, "edgecolor": "none"})


def plot_local_context_map(df, center_lat, center_lon, radii, title, output_path, way_overlays=None):
    """Plot nearby features on a basemap with radius circles and CDE category coloring."""
    # Compute bounds from outermost radius
    max_radius = max(radii)
    delta_lat = max_radius / 111_320.0 * 1.3
    delta_lon = max_radius / (111_320.0 * math.cos(math.radians(center_lat))) * 1.3
    bounds = (center_lat - delta_lat, center_lat + delta_lat,
              center_lon - delta_lon, center_lon + delta_lon)
    basemap_image, basemap_info = build_basemap(bounds)

    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(basemap_image, origin="upper")

    # Draw way overlays
    if way_overlays:
        for way_id, coords in sorted(way_overlays.items()):
            plot_coords = close_coords(coords) if len(coords) >= 3 else coords
            points = geometry_to_pixel_points(plot_coords, basemap_info)
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            color = way_color_hex(way_id)
            if len(coords) >= 3:
                ax.fill(xs, ys, color=color, alpha=0.08)
            ax.plot(xs, ys, color=color, linewidth=2, alpha=0.7, linestyle="-")

    # Draw radius circles
    draw_radius_circles(ax, center_lat, center_lon, radii, basemap_info)

    # Plot features by category
    legend_handles = []
    if not df.empty:
        for cat in CDE_CATEGORY_ORDER:
            subset = df[df["cde_category"] == cat]
            if subset.empty:
                continue
            color = CDE_CATEGORY_COLORS[cat]
            label = CDE_CATEGORY_LABELS[cat]
            pxs = []
            pys = []
            for _, row in subset.iterrows():
                px, py = latlon_to_basemap_pixels(row["lat"], row["lon"], basemap_info)
                pxs.append(px)
                pys.append(py)
            ax.scatter(pxs, pys, s=28, color=color, alpha=0.75, edgecolors="white",
                       linewidths=0.4, zorder=4)
            legend_handles.append(Line2D([0], [0], marker="o", color="none",
                                         markerfacecolor=color, markersize=7,
                                         label=f"{label} ({len(subset)})"))

        # Label named features within 250m
        named = df[(df["name"].astype(str).str.len() > 0) & (df["distance_m"] <= 250)].sort_values("distance_m")
        for _, row in named.head(12).iterrows():
            px, py = latlon_to_basemap_pixels(row["lat"], row["lon"], basemap_info)
            ax.annotate(row["name"], xy=(px, py), xytext=(5, 5), textcoords="offset points",
                        fontsize=6.5, color="#333333",
                        bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "alpha": 0.8, "edgecolor": "none"})

    # Center marker
    cx, cy = latlon_to_basemap_pixels(center_lat, center_lon, basemap_info)
    ax.scatter([cx], [cy], marker="*", s=200, color="#D32F2F", edgecolors="white",
               linewidths=1, zorder=5)

    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_xlim(0, basemap_info["width_px"])
    ax.set_ylim(basemap_info["height_px"], 0)
    ax.set_axis_off()
    if legend_handles:
        # Add way overlay legend entries
        if way_overlays:
            for way_id in sorted(way_overlays.keys()):
                legend_handles.append(Line2D([0], [0], color=way_color_hex(way_id), linewidth=2,
                                             label=way_short_label(way_id)))
        ax.legend(handles=legend_handles, loc="upper right", fontsize=7.5, framealpha=0.95)
    total_text = f"{len(df)} features within {max_radius}m"
    ax.text(0.01, 0.01, total_text, transform=ax.transAxes, fontsize=9,
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "alpha": 0.9, "edgecolor": "#888888"})
    fig.text(0.01, 0.005, "Basemap: OpenStreetMap | Data: Overpass API", fontsize=7)
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def plot_local_context_comparison(df_pre, df_current, center_lat, center_lon, radii,
                                  output_path, way_overlays=None, pre_date_label="",
                                  strike_date_label="", pre_context_label="Pre-strike"):
    """Create a multi-panel comparison of pre-strike vs current local OSM context."""
    max_radius = max(radii)
    delta_lat = max_radius / 111_320.0 * 1.3
    delta_lon = max_radius / (111_320.0 * math.cos(math.radians(center_lat))) * 1.3
    bounds = (center_lat - delta_lat, center_lat + delta_lat,
              center_lon - delta_lon, center_lon + delta_lon)
    basemap_image, basemap_info = build_basemap(bounds)

    fig = plt.figure(figsize=(18, 14))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.4, 1], hspace=0.28, wspace=0.22)
    ax_pre = fig.add_subplot(gs[0, 0])
    ax_cur = fig.add_subplot(gs[0, 1])
    ax_density = fig.add_subplot(gs[1, 0])
    ax_summary = fig.add_subplot(gs[1, 1])

    # --- Top panels: side-by-side context maps ---
    for ax, df, panel_title in [
        (ax_pre, df_pre, f"{pre_context_label} ({pre_date_label})"),
        (ax_cur, df_current, "Current"),
    ]:
        ax.imshow(basemap_image, origin="upper")
        if way_overlays:
            for way_id, coords in sorted(way_overlays.items()):
                plot_coords = close_coords(coords) if len(coords) >= 3 else coords
                points = geometry_to_pixel_points(plot_coords, basemap_info)
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                color = way_color_hex(way_id)
                if len(coords) >= 3:
                    ax.fill(xs, ys, color=color, alpha=0.08)
                ax.plot(xs, ys, color=color, linewidth=2, alpha=0.7)
        draw_radius_circles(ax, center_lat, center_lon, radii, basemap_info)

        if not df.empty:
            for cat in CDE_CATEGORY_ORDER:
                subset = df[df["cde_category"] == cat]
                if subset.empty:
                    continue
                color = CDE_CATEGORY_COLORS[cat]
                pxs, pys = [], []
                for _, row in subset.iterrows():
                    px, py = latlon_to_basemap_pixels(row["lat"], row["lon"], basemap_info)
                    pxs.append(px)
                    pys.append(py)
                ax.scatter(pxs, pys, s=24, color=color, alpha=0.75, edgecolors="white",
                           linewidths=0.3, zorder=4)

        cx, cy = latlon_to_basemap_pixels(center_lat, center_lon, basemap_info)
        ax.scatter([cx], [cy], marker="*", s=150, color="#D32F2F", edgecolors="white",
                   linewidths=1, zorder=5)
        ax.set_title(panel_title, fontsize=11, fontweight="bold")
        ax.set_xlim(0, basemap_info["width_px"])
        ax.set_ylim(basemap_info["height_px"], 0)
        ax.set_axis_off()
        ax.text(0.01, 0.01, f"{len(df)} features", transform=ax.transAxes, fontsize=8,
                bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "alpha": 0.9, "edgecolor": "#888888"})

    # --- Bottom left: feature density bar chart ---
    categories_with_data = []
    for cat in CDE_CATEGORY_ORDER:
        pre_count = int((df_pre["cde_category"] == cat).sum()) if not df_pre.empty else 0
        cur_count = int((df_current["cde_category"] == cat).sum()) if not df_current.empty else 0
        if pre_count > 0 or cur_count > 0:
            categories_with_data.append((cat, pre_count, cur_count))

    if categories_with_data:
        cat_labels = [CDE_CATEGORY_LABELS.get(c[0], c[0]) for c in categories_with_data]
        pre_counts = [c[1] for c in categories_with_data]
        cur_counts = [c[2] for c in categories_with_data]
        x = np.arange(len(cat_labels))
        bar_width = 0.35
        ax_density.bar(x - bar_width / 2, pre_counts, bar_width, label=f"{pre_context_label} ({pre_date_label})",
                       color="#90CAF9", edgecolor="#1565C0", linewidth=0.8)
        ax_density.bar(x + bar_width / 2, cur_counts, bar_width, label="Current",
                       color="#FFCC80", edgecolor="#E65100", linewidth=0.8)
        ax_density.set_xticks(x)
        ax_density.set_xticklabels(cat_labels, rotation=25, ha="right", fontsize=8)
        ax_density.set_ylabel("Feature count")
        ax_density.legend(fontsize=8, framealpha=0.95)
        ax_density.set_title("Feature count by CDE category", fontsize=10, fontweight="bold")
        ax_density.grid(axis="y", linestyle=":", alpha=0.4)
    else:
        ax_density.text(0.5, 0.5, "No features found", fontsize=12, ha="center", va="center",
                        transform=ax_density.transAxes, color="#888888")
        ax_density.set_axis_off()

    # --- Bottom right: CDE summary panel ---
    ax_summary.set_axis_off()
    ax_summary.set_title("CDE information environment summary", fontsize=10, fontweight="bold")

    summary_lines = []
    summary_lines.append(f"Strike date: {strike_date_label}")
    summary_lines.append(f"Historical query: {pre_context_label} ({pre_date_label})")
    summary_lines.append(f"Analysis radius: {max_radius}m")
    summary_lines.append(f"Pre-strike features: {len(df_pre)}")
    summary_lines.append(f"Current features: {len(df_current)}")
    if not df_pre.empty or not df_current.empty:
        comp = compare_prestrike_current(df_pre, df_current)
        if not comp.empty:
            added = int((comp["status"] == "added_after_strike").sum())
            removed = int((comp["status"] == "removed_after_strike").sum())
            unchanged = int((comp["status"] == "present_both").sum())
            summary_lines.append(f"Added after strike: {added}")
            summary_lines.append(f"Removed after strike: {removed}")
            summary_lines.append(f"Unchanged: {unchanged}")

    # Civilian-sensitive pre-strike count
    civ_sens_pre = int((df_pre["cde_category"] == "civilian_sensitive").sum()) if not df_pre.empty else 0
    civ_sens_cur = int((df_current["cde_category"] == "civilian_sensitive").sum()) if not df_current.empty else 0
    mil_pre = int((df_pre["cde_category"] == "military_security").sum()) if not df_pre.empty else 0
    mil_cur = int((df_current["cde_category"] == "military_security").sum()) if not df_current.empty else 0

    summary_lines.append("")
    summary_lines.append("KEY CDE QUESTION:")
    summary_lines.append("Would a pre-strike OSM query have revealed")
    summary_lines.append("civilian-sensitive features nearby?")
    summary_lines.append("")
    if civ_sens_pre > 0:
        summary_lines.append(f"YES: {civ_sens_pre} civilian-sensitive feature(s)")
        summary_lines.append("were present in the pre-strike OSM record.")
    else:
        summary_lines.append("NO: Zero civilian-sensitive features were")
        summary_lines.append("present in the pre-strike OSM record.")
    summary_lines.append(f"(Current record shows {civ_sens_cur})")
    summary_lines.append("")
    summary_lines.append(f"Military/security features: {mil_pre} pre-strike, {mil_cur} current")

    # Named civilian-sensitive features
    if not df_pre.empty:
        named_civ = df_pre[(df_pre["cde_category"] == "civilian_sensitive") & (df_pre["name"].astype(str).str.len() > 0)]
        if not named_civ.empty:
            summary_lines.append("")
            summary_lines.append("Named civilian-sensitive (pre-strike):")
            for _, row in named_civ.head(5).iterrows():
                summary_lines.append(f"  {row['name']} ({row['distance_m']:.0f}m)")

    ax_summary.text(0.05, 0.95, "\n".join(summary_lines), transform=ax_summary.transAxes,
                    fontsize=9, ha="left", va="top", family="monospace",
                    bbox={"boxstyle": "round,pad=0.4", "facecolor": "#FAFAFA",
                          "edgecolor": "#CCCCCC", "alpha": 0.95})

    fig.suptitle(f"Local OSM context: pre-strike vs current  |  Strike: {strike_date_label}",
                 fontsize=13, fontweight="bold", y=0.99)
    fig.text(0.01, 0.005, "Data: Overpass API | Basemap: OpenStreetMap", fontsize=7)
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def extract_buildings_within_boundary(overpass_response, boundary_coords, milestone_key, milestone_row,
                                      reference_polygons):
    """Return building-tagged OSM elements whose centers fall inside the boundary polygon."""
    if not boundary_coords or len(boundary_coords) < 3:
        return []

    boundary_centroid = geometry_centroid(boundary_coords) or boundary_coords[0]
    boundary_area_sq_m = polygon_area_sq_m(boundary_coords)
    boundary_perimeter_m = polygon_perimeter_m(boundary_coords)
    elements = overpass_response.get("elements", [])
    features = []

    for elem in elements:
        elem_type = elem.get("type", "")
        osm_id = elem.get("id")
        tags = elem.get("tags", {})
        if elem_type == "node":
            lat = elem.get("lat")
            lon = elem.get("lon")
        else:
            center = elem.get("center", {})
            lat = center.get("lat")
            lon = center.get("lon")

        if lat is None or lon is None:
            continue

        point = (lat, lon)
        if not point_in_polygon(point, boundary_coords):
            continue

        xy = latlon_to_local_xy([point], boundary_centroid)
        distance_m = math.hypot(xy[0][0], xy[0][1]) if xy else None
        later_assignment = classify_building_assignment(point, reference_polygons)
        building_semantics = classify_building_semantics(tags)

        features.append(
            {
                "stage_key": milestone_key,
                "stage_label": MILESTONE_LABELS[milestone_key],
                "boundary_way_id": BUILDING_AUDIT_WAY_ID,
                "boundary_way_label": way_label(BUILDING_AUDIT_WAY_ID),
                "boundary_version": int(milestone_row["version"]),
                "snapshot_timestamp": milestone_row["timestamp"],
                "snapshot_timestamp_iso": iso_timestamp(milestone_row["timestamp"]),
                "boundary_area_sq_m": safe_float(boundary_area_sq_m),
                "boundary_perimeter_m": safe_float(boundary_perimeter_m),
                "element_type": elem_type,
                "osm_id": osm_id,
                "name": feature_name(tags),
                "lat": lat,
                "lon": lon,
                "distance_to_boundary_centroid_m": round(distance_m, 1) if distance_m is not None else None,
                "building_tag": tags.get("building", ""),
                "building_semantics": building_semantics,
                "building_semantics_label": BUILDING_SEMANTIC_LABELS[building_semantics],
                "later_assignment": later_assignment,
                "later_assignment_label": BUILDING_ASSIGNMENT_LABELS[later_assignment],
                "cde_category": classify_osm_element(tags),
                "tags_str": "; ".join(f"{k}={v}" for k, v in sorted(tags.items())),
            }
        )

    sort_order = {key: index for index, key in enumerate(BUILDING_ASSIGNMENT_ORDER)}
    features.sort(
        key=lambda item: (
            sort_order.get(item["later_assignment"], 999),
            item["distance_to_boundary_centroid_m"] if item["distance_to_boundary_centroid_m"] is not None else 999999,
            item["element_type"],
            item["osm_id"] or 0,
        )
    )
    for index, feature in enumerate(features, start=1):
        feature["stage_rank"] = index
    return features


def summarise_building_stage(milestone_key, milestone_row, stage_features, query_failed=False, query_error=""):
    assignment_counts = {key: 0 for key in BUILDING_ASSIGNMENT_ORDER}
    semantic_counts = {key: 0 for key in BUILDING_SEMANTIC_ORDER}
    for feature in stage_features:
        assignment_counts[feature["later_assignment"]] = assignment_counts.get(feature["later_assignment"], 0) + 1
        semantic_counts[feature["building_semantics"]] = semantic_counts.get(feature["building_semantics"], 0) + 1

    row = {
        "stage_key": milestone_key,
        "stage_label": MILESTONE_LABELS[milestone_key],
        "boundary_version": int(milestone_row["version"]),
        "boundary_timestamp": milestone_row["timestamp"],
        "boundary_timestamp_iso": iso_timestamp(milestone_row["timestamp"]),
        "building_count": len(stage_features),
        "named_building_count": int(sum(1 for feature in stage_features if feature["name"])),
        "query_failed": bool(query_failed),
        "query_error": query_error or "",
        "reassignment_risk_count": int(
            assignment_counts["later_school_polygon"]
            + assignment_counts["later_compound_polygon"]
            + assignment_counts["outside_latest_barracks"]
        ),
    }
    for key in BUILDING_ASSIGNMENT_ORDER:
        row[f"{key}_count"] = int(assignment_counts[key])
    for key in BUILDING_SEMANTIC_ORDER:
        row[f"{key}_count"] = int(semantic_counts[key])
    return row


def build_building_presence_df(buildings_df):
    if buildings_df.empty:
        return pd.DataFrame(
            columns=[
                "element_type",
                "osm_id",
                "name",
                "building_tag",
                "building_semantics",
                "building_semantics_label",
                "later_assignment",
                "later_assignment_label",
                "tags_str",
                "stages_present_count",
                "stages_present",
                "first_seen_stage",
                "last_seen_stage",
            ]
            + MILESTONE_ORDER
        )

    key_cols = ["element_type", "osm_id"]
    ordered = buildings_df.sort_values(["snapshot_timestamp", "stage_rank"]).copy()
    latest_meta = (
        ordered.groupby(key_cols, as_index=False)
        .tail(1)[
            [
                "element_type",
                "osm_id",
                "name",
                "building_tag",
                "building_semantics",
                "building_semantics_label",
                "later_assignment",
                "later_assignment_label",
                "tags_str",
            ]
        ]
    )

    presence = ordered.assign(present=True).pivot_table(
        index=key_cols,
        columns="stage_key",
        values="present",
        aggfunc="max",
        fill_value=False,
    )
    presence = presence.reset_index()
    for milestone_key in MILESTONE_ORDER:
        if milestone_key not in presence.columns:
            presence[milestone_key] = False
        presence[milestone_key] = presence[milestone_key].astype(bool)

    result = latest_meta.merge(presence, on=key_cols, how="left")
    result["stages_present_count"] = result[MILESTONE_ORDER].sum(axis=1)
    result["stages_present"] = result.apply(
        lambda row: ", ".join(MILESTONE_LABELS[key] for key in MILESTONE_ORDER if bool(row[key])),
        axis=1,
    )
    result["first_seen_stage"] = result.apply(
        lambda row: next((MILESTONE_LABELS[key] for key in MILESTONE_ORDER if bool(row[key])), ""),
        axis=1,
    )
    result["last_seen_stage"] = result.apply(
        lambda row: next((MILESTONE_LABELS[key] for key in reversed(MILESTONE_ORDER) if bool(row[key])), ""),
        axis=1,
    )
    return result.sort_values(
        ["later_assignment", "building_semantics", "element_type", "osm_id"]
    ).reset_index(drop=True)


def build_building_audit_summary(stage_df, presence_df):
    summary = {
        "site_way_id": BUILDING_AUDIT_WAY_ID,
        "site_label": way_label(BUILDING_AUDIT_WAY_ID),
        "method": {
            "snapshot_source": "Overpass historical snapshots taken at each barracks milestone timestamp",
            "inclusion_rule": "building-tagged OSM elements whose center lies inside the barracks boundary polygon at that stage",
            "later_assignment_rule": "each returned building center is then classified against the latest school, compound, and barracks polygons to show later clarification",
        },
        "stages": [],
        "unique_building_elements": 0 if presence_df.empty else int(len(presence_df)),
        "findings": [],
    }

    if not stage_df.empty:
        ordered = stage_df.copy()
        ordered["stage_order"] = ordered["stage_key"].map({key: index for index, key in enumerate(MILESTONE_ORDER)})
        ordered = ordered.sort_values("stage_order")
        summary["stages"] = [
            {key: json_ready(value) for key, value in row.items() if key != "stage_order"}
            for row in ordered.to_dict(orient="records")
        ]
        stage_by_key = {row["stage_key"]: row for row in ordered.to_dict(orient="records")}
        failed_stages = [row for row in ordered.to_dict(orient="records") if row.get("query_failed")]
        if failed_stages:
            failed_labels = ", ".join(row["stage_label"] for row in failed_stages)
            summary["findings"].append(
                f"Overpass did not return a usable building snapshot for: {failed_labels}. Those stages are marked as unavailable rather than interpreted as true zero-building results."
            )

        pre_row = stage_by_key.get("last_pre_strike") or stage_by_key.get("first_version")
        post_row = stage_by_key.get("first_post_strike")
        latest_row = stage_by_key.get("latest")

        if pre_row is not None:
            if pre_row.get("query_failed"):
                summary["findings"].append(
                    f"The {pre_row['stage_label'].lower()} building snapshot could not be fetched cleanly from Overpass, so the pre-strike building count should be treated as unavailable."
                )
            else:
                summary["findings"].append(
                    f"A barracks-boundary building query at {pre_row['stage_label'].lower()} would have returned "
                    f"{int(pre_row['building_count'])} building-tagged OSM element(s)."
                )
                if int(pre_row["reassignment_risk_count"]) > 0:
                    summary["findings"].append(
                        f"Of those pre-strike returned buildings, {int(pre_row['later_school_polygon_count'])} later fall "
                        f"inside the mapped school polygon, {int(pre_row['later_compound_polygon_count'])} inside the "
                        f"mapped compound polygon, and {int(pre_row['outside_latest_barracks_count'])} outside the latest "
                        f"barracks boundary."
                    )
                if int(pre_row["generic_building_count"]) > 0:
                    summary["findings"].append(
                        f"The pre-strike returned set is dominated by generic building tags: "
                        f"{int(pre_row['generic_building_count'])} of {int(pre_row['building_count'])} are simply generic "
                        f"building features rather than explicitly school- or military-tagged structures."
                    )

        if (
            pre_row is not None
            and latest_row is not None
            and not pre_row.get("query_failed")
            and not latest_row.get("query_failed")
            and int(pre_row["building_count"]) != int(latest_row["building_count"])
        ):
            summary["findings"].append(
                f"The number of buildings returned by the barracks boundary changes from {int(pre_row['building_count'])} "
                f"at the pre-strike stage to {int(latest_row['building_count'])} at the latest stage, showing that the "
                f"site boundary revision materially changes which buildings are swept into the site list."
            )

        if (
            post_row is not None
            and not post_row.get("query_failed")
            and int(post_row["later_school_polygon_count"]) == 0
            and int(post_row["later_compound_polygon_count"]) == 0
        ):
            summary["findings"].append(
                "Once the post-strike barracks boundary is introduced, the returned building set no longer includes any "
                "building centers that later fall inside the separately mapped school or compound polygons."
            )

    return summary


def build_building_replay_summary(stage_df, presence_df):
    summary = {
        "stages": [],
        "unique_building_elements": 0 if presence_df.empty else int(len(presence_df)),
        "findings": [],
        "method": {
            "snapshot_source": "Latest building-tagged OSM layer replayed against each historical barracks boundary",
            "inclusion_rule": "latest building-tagged OSM elements whose centers would fall inside each barracks boundary stage",
            "purpose": "proof-of-concept showing how boundary-only listing could sweep later-clarified buildings into the site list",
        },
    }

    if stage_df.empty:
        return summary

    ordered = stage_df.copy()
    ordered["stage_order"] = ordered["stage_key"].map({key: index for index, key in enumerate(MILESTONE_ORDER)})
    ordered = ordered.sort_values("stage_order")
    summary["stages"] = [
        {key: json_ready(value) for key, value in row.items() if key != "stage_order"}
        for row in ordered.to_dict(orient="records")
    ]
    stage_by_key = {row["stage_key"]: row for row in ordered.to_dict(orient="records")}

    pre_row = stage_by_key.get("last_pre_strike") or stage_by_key.get("first_version")
    post_row = stage_by_key.get("first_post_strike")
    latest_row = stage_by_key.get("latest")

    if pre_row is not None and not pre_row.get("query_failed"):
        summary["findings"].append(
            f"Using the latest building layer as a fixed reference, the pre-strike barracks boundary would sweep "
            f"{int(pre_row['building_count'])} building-tagged OSM element(s) into the site list."
        )
        if int(pre_row["reassignment_risk_count"]) > 0:
            summary["findings"].append(
                f"That replayed pre-strike list would include {int(pre_row['later_school_polygon_count'])} building(s) "
                f"in the later school polygon, {int(pre_row['later_compound_polygon_count'])} in the later compound "
                f"polygon, and {int(pre_row['outside_latest_barracks_count'])} that fall outside the latest barracks boundary."
            )

    if (
        pre_row is not None
        and post_row is not None
        and not pre_row.get("query_failed")
        and not post_row.get("query_failed")
        and int(pre_row["building_count"]) != int(post_row["building_count"])
    ):
        summary["findings"].append(
            f"Once the barracks boundary is revised to its first post-strike shape, the replayed building count changes from "
            f"{int(pre_row['building_count'])} to {int(post_row['building_count'])}, showing how the boundary update alone "
            f"changes the list of buildings captured by a site query."
        )

    if (
        latest_row is not None
        and pre_row is not None
        and not latest_row.get("query_failed")
        and not pre_row.get("query_failed")
        and int(pre_row["building_count"]) != int(latest_row["building_count"])
    ):
        summary["findings"].append(
            f"By the latest boundary stage, the replayed building list settles at {int(latest_row['building_count'])} "
            f"building(s), compared with {int(pre_row['building_count'])} under the broader pre-strike boundary."
        )

    return summary


def plot_building_stage_maps(stage_df, buildings_df, stage_geometries, reference_polygons, output_path):
    if not stage_geometries:
        save_note_figure(output_path, "Boundary building audit", "No stage geometries were available for the building audit.")
        return

    all_coords = []
    for coords in stage_geometries.values():
        all_coords.extend(coords)
    for coords in reference_polygons.values():
        all_coords.extend(coords)
    if not buildings_df.empty:
        all_coords.extend(list(zip(buildings_df["lat"], buildings_df["lon"])))

    if not all_coords:
        save_note_figure(output_path, "Boundary building audit", "No geometry or building points were available for plotting.")
        return

    basemap_image, basemap_info = build_basemap(expand_bounds(all_coords, padding_ratio=0.18))
    fig, axes = plt.subplots(2, 2, figsize=(16, 13))
    axes = axes.ravel()
    stage_rows = {
        row["stage_key"]: row
        for row in stage_df.to_dict(orient="records")
    }

    for ax, milestone_key in zip(axes, MILESTONE_ORDER):
        ax.imshow(basemap_image, origin="upper")
        stage_row = stage_rows.get(milestone_key)
        boundary_coords = stage_geometries.get(milestone_key, [])

        if len(boundary_coords) >= 3:
            boundary_points = geometry_to_pixel_points(close_coords(boundary_coords), basemap_info)
            xs = [point[0] for point in boundary_points]
            ys = [point[1] for point in boundary_points]
            ax.fill(xs, ys, color=way_color_hex(BUILDING_AUDIT_WAY_ID), alpha=0.16)
            ax.plot(xs, ys, color=way_color_hex(BUILDING_AUDIT_WAY_ID), linewidth=2.8, alpha=0.95)

        latest_barracks = reference_polygons.get("barracks", [])
        if len(latest_barracks) >= 3:
            points = geometry_to_pixel_points(close_coords(latest_barracks), basemap_info)
            ax.plot(
                [point[0] for point in points],
                [point[1] for point in points],
                color="#8D6E63",
                linewidth=1.6,
                linestyle=":",
                alpha=0.9,
            )

        for polygon_key, color in [("school", "#1976D2"), ("compound", "#00ACC1")]:
            coords = reference_polygons.get(polygon_key, [])
            if len(coords) < 3:
                continue
            points = geometry_to_pixel_points(close_coords(coords), basemap_info)
            ax.plot(
                [point[0] for point in points],
                [point[1] for point in points],
                color=color,
                linewidth=1.8,
                linestyle="--",
                alpha=0.95,
            )

        if not buildings_df.empty:
            subset = buildings_df[buildings_df["stage_key"] == milestone_key]
            for assignment in BUILDING_ASSIGNMENT_ORDER:
                assigned = subset[subset["later_assignment"] == assignment]
                if assigned.empty:
                    continue
                pxs = []
                pys = []
                for _, row in assigned.iterrows():
                    px, py = latlon_to_basemap_pixels(row["lat"], row["lon"], basemap_info)
                    pxs.append(px)
                    pys.append(py)
                ax.scatter(
                    pxs,
                    pys,
                    s=42,
                    color=BUILDING_ASSIGNMENT_COLORS[assignment],
                    edgecolors="white",
                    linewidths=0.5,
                    alpha=0.9,
                    zorder=5,
                )

        if stage_row is not None:
            title = (
                f"{stage_row['stage_label']}\n"
                f"v{int(stage_row['boundary_version'])} | {format_date(stage_row['boundary_timestamp'])} | "
                f"{int(stage_row['building_count'])} building(s)"
            )
            if stage_row.get("query_failed"):
                count_lines = [
                    "Snapshot unavailable",
                    compact_text(stage_row.get("query_error", ""), 72),
                ]
            else:
                count_lines = [
                    f"Later school: {int(stage_row['later_school_polygon_count'])}",
                    f"Later compound: {int(stage_row['later_compound_polygon_count'])}",
                    f"Barracks-only: {int(stage_row['latest_barracks_only_count'])}",
                    f"Outside latest: {int(stage_row['outside_latest_barracks_count'])}",
                ]
            ax.text(
                0.01,
                0.99,
                "\n".join(count_lines),
                transform=ax.transAxes,
                fontsize=8.2,
                ha="left",
                va="top",
                bbox={"boxstyle": "round,pad=0.22", "facecolor": "white", "alpha": 0.92, "edgecolor": "#888888"},
            )
        else:
            title = f"{MILESTONE_LABELS[milestone_key]}\nNo barracks state available"

        ax.set_title(title, fontsize=10.5, fontweight="bold")
        ax.set_xlim(0, basemap_info["width_px"])
        ax.set_ylim(basemap_info["height_px"], 0)
        ax.set_axis_off()

    legend_handles = [
        Line2D([0], [0], color=way_color_hex(BUILDING_AUDIT_WAY_ID), linewidth=2.8, label="Barracks boundary at that stage"),
        Line2D([0], [0], color="#8D6E63", linewidth=1.6, linestyle=":", label="Latest barracks boundary"),
        Line2D([0], [0], color="#1976D2", linewidth=1.8, linestyle="--", label="Latest school polygon"),
        Line2D([0], [0], color="#00ACC1", linewidth=1.8, linestyle="--", label="Latest compound polygon"),
    ]
    legend_handles.extend(
        Line2D(
            [0],
            [0],
            marker="o",
            color="none",
            markerfacecolor=BUILDING_ASSIGNMENT_COLORS[key],
            markeredgecolor="white",
            markersize=8,
            label=BUILDING_ASSIGNMENT_LABELS[key],
        )
        for key in BUILDING_ASSIGNMENT_ORDER
    )
    fig.legend(handles=legend_handles, loc="lower center", ncol=4, framealpha=0.96, fontsize=8.5)
    fig.suptitle("Latest building layer replayed against each barracks boundary stage", fontsize=13, fontweight="bold", y=0.98)
    fig.text(
        0.01,
        0.015,
        "Points show building-tagged OSM elements from the latest available building layer, replayed against each "
        "barracks boundary stage. Dashed outlines show the later school / compound clarification for audit context.",
        fontsize=8,
    )
    fig.text(0.01, 0.003, "Basemap: OpenStreetMap | Data: Overpass latest-building replay", fontsize=7)
    plt.tight_layout(rect=(0, 0.06, 1, 0.96))
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def plot_building_stage_counts(stage_df, output_path):
    if stage_df.empty:
        save_note_figure(output_path, "Boundary building counts", "No building-stage summary rows were available.")
        return

    ordered = stage_df.copy()
    ordered["stage_order"] = ordered["stage_key"].map({key: index for index, key in enumerate(MILESTONE_ORDER)})
    ordered = ordered.sort_values("stage_order")
    labels = [row["stage_label"].replace(" ", "\n") for _, row in ordered.iterrows()]
    x = np.arange(len(labels))

    fig, (ax_assignment, ax_semantics) = plt.subplots(1, 2, figsize=(15, 5.8), width_ratios=[1.25, 1])

    bottom = np.zeros(len(ordered))
    for key in BUILDING_ASSIGNMENT_ORDER:
        values = ordered[f"{key}_count"].to_numpy()
        ax_assignment.bar(
            x,
            values,
            bottom=bottom,
            color=BUILDING_ASSIGNMENT_COLORS[key],
            edgecolor="white",
            linewidth=0.6,
            label=BUILDING_ASSIGNMENT_LABELS[key],
        )
        bottom += values
    for idx, total in enumerate(ordered["building_count"].to_numpy()):
        ax_assignment.text(idx, total + 0.15, str(int(total)), ha="center", va="bottom", fontsize=8)
    for idx, failed in enumerate(ordered["query_failed"].to_numpy()):
        if failed:
            ax_assignment.text(idx, 0.3, "query\nfailed", ha="center", va="bottom", fontsize=7.5, color="#C62828")
    ax_assignment.set_xticks(x)
    ax_assignment.set_xticklabels(labels, fontsize=8)
    ax_assignment.set_ylabel("Captured building count")
    ax_assignment.set_title("Replayed buildings by later assignment", fontsize=10.5, fontweight="bold")
    ax_assignment.grid(axis="y", linestyle=":", alpha=0.35)
    ax_assignment.legend(fontsize=7.8, framealpha=0.95, loc="upper right")

    bottom = np.zeros(len(ordered))
    for key in BUILDING_SEMANTIC_ORDER:
        values = ordered[f"{key}_count"].to_numpy()
        ax_semantics.bar(
            x,
            values,
            bottom=bottom,
            color=BUILDING_SEMANTIC_COLORS[key],
            edgecolor="white",
            linewidth=0.6,
            label=BUILDING_SEMANTIC_LABELS[key],
        )
        bottom += values
    for idx, total in enumerate(ordered["building_count"].to_numpy()):
        ax_semantics.text(idx, total + 0.15, str(int(total)), ha="center", va="bottom", fontsize=8)
    for idx, failed in enumerate(ordered["query_failed"].to_numpy()):
        if failed:
            ax_semantics.text(idx, 0.3, "query\nfailed", ha="center", va="bottom", fontsize=7.5, color="#C62828")
    ax_semantics.set_xticks(x)
    ax_semantics.set_xticklabels(labels, fontsize=8)
    ax_semantics.set_ylabel("Captured building count")
    ax_semantics.set_title("Replayed buildings by tag semantics", fontsize=10.5, fontweight="bold")
    ax_semantics.grid(axis="y", linestyle=":", alpha=0.35)
    ax_semantics.legend(fontsize=7.8, framealpha=0.95, loc="upper right")

    fig.suptitle("Latest building layer replay across barracks boundary stages", fontsize=13, fontweight="bold", y=0.98)
    fig.text(0.01, 0.01, "Counts use the latest building-tagged OSM elements, replayed against each stage boundary.", fontsize=8)
    plt.tight_layout(rect=(0, 0.04, 1, 0.95))
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()


def collect_building_boundary_audit(all_geometries, all_milestones, output_dir):
    stage_rows = []
    building_rows = []
    replay_stage_rows = []
    replay_building_rows = []
    generated_files = []
    stage_geometries = {}

    reference_polygons = {}
    for role_name, way_id in [("school", 1484791929), ("compound", 1485767423), ("barracks", BUILDING_AUDIT_WAY_ID)]:
        latest_row = all_milestones[way_id]["latest"]
        coords = all_geometries[way_id].get(int(latest_row["version"]), []) if latest_row is not None else []
        reference_polygons[role_name] = coords

    for milestone_key in MILESTONE_ORDER:
        milestone_row = all_milestones[BUILDING_AUDIT_WAY_ID].get(milestone_key)
        if milestone_row is None:
            continue
        coords = all_geometries[BUILDING_AUDIT_WAY_ID].get(int(milestone_row["version"]), [])
        if len(coords) < 3:
            continue
        stage_geometries[milestone_key] = coords
        query_bounds = expand_bounds(coords, padding_ratio=0.02, min_padding_deg=0.0002)
        query_date_iso = pd.Timestamp(milestone_row["timestamp"]).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        print(
            f"Querying building-tagged OSM elements inside {way_short_label(BUILDING_AUDIT_WAY_ID)} "
            f"{MILESTONE_LABELS[milestone_key].lower()} ({query_date_iso})..."
        )
        query = build_overpass_bbox_query(query_bounds, date_iso=query_date_iso, tag_filter=BUILDING_AUDIT_TAG_FILTER)
        response = fetch_overpass(query)
        stage_features = extract_buildings_within_boundary(
            response,
            coords,
            milestone_key,
            milestone_row,
            reference_polygons,
        )
        building_rows.extend(stage_features)
        stage_rows.append(
            summarise_building_stage(
                milestone_key,
                milestone_row,
                stage_features,
                query_failed=bool(response.get("_fetch_failed")),
                query_error=response.get("_error", ""),
            )
        )
        time.sleep(2)

    buildings_df = pd.DataFrame(building_rows)
    if not buildings_df.empty:
        buildings_df["snapshot_timestamp"] = pd.to_datetime(buildings_df["snapshot_timestamp"], utc=True, errors="coerce")

    stage_df = pd.DataFrame(stage_rows)
    if not stage_df.empty:
        stage_df["boundary_timestamp"] = pd.to_datetime(stage_df["boundary_timestamp"], utc=True, errors="coerce")
        stage_df["stage_order"] = stage_df["stage_key"].map({key: index for index, key in enumerate(MILESTONE_ORDER)})
        stage_df = stage_df.sort_values("stage_order").drop(columns=["stage_order"]).reset_index(drop=True)

    presence_df = build_building_presence_df(buildings_df)
    historical_summary = build_building_audit_summary(stage_df, presence_df)

    # Replay the latest building layer against each boundary stage to show boundary-only capture risk.
    replay_summary = {"stages": [], "unique_building_elements": 0, "findings": [], "method": {}}
    replay_df = pd.DataFrame()
    replay_presence_df = pd.DataFrame()
    latest_boundary_row = all_milestones[BUILDING_AUDIT_WAY_ID]["latest"]
    all_stage_coords = []
    for coords in stage_geometries.values():
        all_stage_coords.extend(coords)
    for coords in reference_polygons.values():
        all_stage_coords.extend(coords)
    if latest_boundary_row is not None and all_stage_coords:
        replay_bounds = expand_bounds(all_stage_coords, padding_ratio=0.05, min_padding_deg=0.0003)
        replay_date_iso = pd.Timestamp(latest_boundary_row["timestamp"]).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"Querying latest building layer for boundary replay ({replay_date_iso})...")
        replay_response = fetch_overpass(
            build_overpass_bbox_query(replay_bounds, date_iso=replay_date_iso, tag_filter=BUILDING_AUDIT_TAG_FILTER)
        )
        for milestone_key in MILESTONE_ORDER:
            milestone_row = all_milestones[BUILDING_AUDIT_WAY_ID].get(milestone_key)
            coords = stage_geometries.get(milestone_key, [])
            if milestone_row is None or len(coords) < 3:
                continue
            replay_features = extract_buildings_within_boundary(
                replay_response,
                coords,
                milestone_key,
                milestone_row,
                reference_polygons,
            )
            replay_building_rows.extend(replay_features)
            replay_stage_rows.append(
                summarise_building_stage(
                    milestone_key,
                    milestone_row,
                    replay_features,
                    query_failed=bool(replay_response.get("_fetch_failed")),
                    query_error=replay_response.get("_error", ""),
                )
            )

        replay_df = pd.DataFrame(replay_building_rows)
        if not replay_df.empty:
            replay_df["snapshot_timestamp"] = pd.to_datetime(replay_df["snapshot_timestamp"], utc=True, errors="coerce")
        replay_stage_df = pd.DataFrame(replay_stage_rows)
        if not replay_stage_df.empty:
            replay_stage_df["boundary_timestamp"] = pd.to_datetime(replay_stage_df["boundary_timestamp"], utc=True, errors="coerce")
            replay_stage_df["stage_order"] = replay_stage_df["stage_key"].map({key: index for index, key in enumerate(MILESTONE_ORDER)})
            replay_stage_df = replay_stage_df.sort_values("stage_order").drop(columns=["stage_order"]).reset_index(drop=True)
        replay_presence_df = build_building_presence_df(replay_df)
        replay_summary = build_building_replay_summary(replay_stage_df, replay_presence_df)
    else:
        replay_stage_df = pd.DataFrame()

    summary = {
        "site_way_id": BUILDING_AUDIT_WAY_ID,
        "site_label": way_label(BUILDING_AUDIT_WAY_ID),
        "method": historical_summary.get("method", {}),
        "stages": historical_summary.get("stages", []),
        "unique_building_elements": historical_summary.get("unique_building_elements", 0),
        "findings": replay_summary.get("findings", []) + historical_summary.get("findings", [])[:2],
        "historical_findings": historical_summary.get("findings", []),
        "historical_snapshot_stages": historical_summary.get("stages", []),
        "historical_unique_building_elements": historical_summary.get("unique_building_elements", 0),
        "replay_method": replay_summary.get("method", {}),
        "replay_findings": replay_summary.get("findings", []),
        "replay_stages": replay_summary.get("stages", []),
        "replay_unique_building_elements": replay_summary.get("unique_building_elements", 0),
    }

    by_stage_csv = output_dir / "site_boundary_buildings_by_stage.csv"
    buildings_df.to_csv(by_stage_csv, index=False)
    generated_files.append(by_stage_csv)

    stage_summary_csv = output_dir / "site_boundary_building_stage_summary.csv"
    stage_df.to_csv(stage_summary_csv, index=False)
    generated_files.append(stage_summary_csv)

    presence_csv = output_dir / "site_boundary_building_presence.csv"
    presence_df.to_csv(presence_csv, index=False)
    generated_files.append(presence_csv)

    stage_map_path = output_dir / "site_boundary_building_stages.png"
    plot_building_stage_maps(replay_stage_df, replay_df, stage_geometries, reference_polygons, stage_map_path)
    generated_files.append(stage_map_path)

    stage_counts_path = output_dir / "site_boundary_building_counts.png"
    plot_building_stage_counts(replay_stage_df, stage_counts_path)
    generated_files.append(stage_counts_path)

    replay_by_stage_csv = output_dir / "site_boundary_building_replay_by_stage.csv"
    replay_df.to_csv(replay_by_stage_csv, index=False)
    generated_files.append(replay_by_stage_csv)

    replay_stage_summary_csv = output_dir / "site_boundary_building_replay_stage_summary.csv"
    replay_stage_df.to_csv(replay_stage_summary_csv, index=False)
    generated_files.append(replay_stage_summary_csv)

    replay_presence_csv = output_dir / "site_boundary_building_replay_presence.csv"
    replay_presence_df.to_csv(replay_presence_csv, index=False)
    generated_files.append(replay_presence_csv)

    summary_path = output_dir / "site_boundary_buildings_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    generated_files.append(summary_path)

    return {
        "stage_df": stage_df,
        "buildings_df": buildings_df,
        "presence_df": presence_df,
        "replay_stage_df": replay_stage_df,
        "replay_df": replay_df,
        "replay_presence_df": replay_presence_df,
        "summary": summary,
        "generated_files": generated_files,
    }


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
    if summary.get("latest_area_sq_m") is not None:
        sentences.append(f"Its latest area is approximately {summary['latest_area_sq_m']:,.0f} sq m.")
    if summary["major_tag_events"]:
        sentences.append(f"Major semantic edits: {summary['major_tag_events']}.")
    return " ".join(sentences)


def generate_key_findings(summary_by_way, conflation, strike_timestamp, building_audit_summary=None):
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

    frac = conflation.get("school_in_barracks_pre_fraction")
    if frac is not None and frac > 0.5:
        findings.append(f"Spatial containment check: {int(frac * 100)}% of the school's vertices fall inside the pre-strike barracks boundary, confirming the school location was subsumed within the undifferentiated military perimeter before the strike.")

    school_compound_shared = conflation.get("shared_nodes", {}).get("school_compound", 0)
    if school_compound_shared > 0:
        findings.append(f"The school and compound polygons share {school_compound_shared} boundary node(s), meaning they were explicitly drawn as adjacent with touching edges in the post-strike OSM record.")

    if building_audit_summary is not None:
        findings.extend(building_audit_summary.get("findings", [])[:3])

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


def generate_readme(root_dir, output_dir, strike_timestamp, summary_df, summary_by_way, key_findings, conflation, generated_files, local_context_summary=None, building_audit_summary=None):
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

    pre_strike_date_text = (
        f"Barracks last pre-strike state: {format_timestamp(barracks['last_pre_strike_timestamp'])}; "
        f"School: not mapped before strike; Military base: not mapped before strike."
    )
    first_post_date_text = (
        f"Barracks first post-strike state: {format_timestamp(barracks['first_post_strike_timestamp'])}; "
        f"School first post-strike state: {format_timestamp(school['first_post_strike_timestamp'])}; "
        f"Military base first post-strike state: {format_timestamp(compound['first_post_strike_timestamp'])}."
    )

    persisted_outputs = list(generated_files)
    animation_path = output_dir / "way_history_animation.gif"
    if animation_path.exists():
        persisted_outputs.append(animation_path)
    important_outputs = sorted(
        repo_relative(path, root_dir)
        for path in {Path(p) for p in persisted_outputs}
        if path.suffix.lower() in {".csv", ".json", ".txt", ".png", ".gif", ".md"}
    )
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
            f"This is the key pre-strike reference image. In the current history, {pre_strike_absence_text} are not yet present here as separate ways, while the broader barracks boundary remains the main military-tagged pre-strike polygon. {pre_strike_date_text}",
            "",
            f"![First post-strike combined map]({repo_relative(output_dir / 'state_maps' / 'first_post_strike_combined.png', root_dir)})",
            "",
            f"This panel shows the first available post-strike state for each way. It makes the map clarification visible by showing when the school and smaller military-base polygons first appear as distinct objects in OSM. {first_post_date_text}",
            "",
            f"![Before vs after comparison]({repo_relative(output_dir / 'before_after_comparison.png', root_dir)})",
            "",
            "This side-by-side comparison is the key visual. The left panel shows the last pre-strike OSM state; the right panel shows the first post-strike state. The difference makes immediately visible how the map record changed after the strike.",
            "",
            "## Spatial Analysis",
            "",
        ]
    )

    frac = conflation.get("school_in_barracks_pre_fraction")
    if frac is not None and frac > 0.0:
        lines.append(f"- **Containment**: {int(frac * 100)}% of the latest school polygon's vertices fall inside the pre-strike barracks boundary. Before the strike, the school's location was part of an undifferentiated military-tagged area in OSM.")
    for key, count in conflation.get("shared_nodes", {}).items():
        if count > 0:
            lines.append(f"- **Shared boundary nodes** ({key.replace('_', ' ')}): {count} node(s) shared, confirming these polygons were drawn with touching or coincident edges.")
    if conflation.get("school_area_sq_m") is not None:
        lines.append(f"- **School area**: {conflation['school_area_sq_m']:,.0f} sq m (latest polygon)")
    if conflation.get("barracks_pre_area_sq_m") is not None:
        lines.append(f"- **Pre-strike barracks area**: {conflation['barracks_pre_area_sq_m']:,.0f} sq m")
    if conflation.get("latest_school_compound_distance_m") is not None:
        d = conflation["latest_school_compound_distance_m"]
        lines.append(f"- **School-to-compound distance**: {d:.1f} m" + (" (shared boundary)" if d < 1.0 else ""))
    if conflation.get("latest_school_barracks_distance_m") is not None:
        d = conflation["latest_school_barracks_distance_m"]
        lines.append(f"- **School-to-barracks distance**: {d:.1f} m" + (" (shared boundary)" if d < 1.0 else ""))

    lines.extend(
        [
            "",
            f"![Conflation risk summary]({repo_relative(output_dir / 'conflation_risk.png', root_dir)})",
            "",
            f"This figure turns the edit history and spatial analysis into a brief interpretive summary. The current qualitative audit rating is {conflation['overall_rating']}, driven by the combination of pre-strike mapping gaps and post-strike clarification.",
            "",
        ]
    )

    if building_audit_summary is not None:
        building_stage_rows = []
        for row in building_audit_summary.get("historical_snapshot_stages", building_audit_summary.get("stages", [])):
            building_stage_rows.append(
                {
                    "Stage": row["stage_label"],
                    "Boundary": f"v{int(row['boundary_version'])} ({format_timestamp(row['boundary_timestamp'])})",
                    "Snapshot": "unavailable" if row.get("query_failed") else "ok",
                    "Buildings returned": "n/a" if row.get("query_failed") else int(row["building_count"]),
                    "Later school polygon": "n/a" if row.get("query_failed") else int(row["later_school_polygon_count"]),
                    "Later compound polygon": "n/a" if row.get("query_failed") else int(row["later_compound_polygon_count"]),
                    "Barracks-only": "n/a" if row.get("query_failed") else int(row["latest_barracks_only_count"]),
                    "Outside latest": "n/a" if row.get("query_failed") else int(row["outside_latest_barracks_count"]),
                }
            )
        replay_stage_rows = []
        for row in building_audit_summary.get("replay_stages", []):
            replay_stage_rows.append(
                {
                    "Stage": row["stage_label"],
                    "Boundary": f"v{int(row['boundary_version'])} ({format_timestamp(row['boundary_timestamp'])})",
                    "Buildings captured": "n/a" if row.get("query_failed") else int(row["building_count"]),
                    "Later school polygon": "n/a" if row.get("query_failed") else int(row["later_school_polygon_count"]),
                    "Later compound polygon": "n/a" if row.get("query_failed") else int(row["later_compound_polygon_count"]),
                    "Barracks-only": "n/a" if row.get("query_failed") else int(row["latest_barracks_only_count"]),
                    "Outside latest": "n/a" if row.get("query_failed") else int(row["outside_latest_barracks_count"]),
                }
            )

        lines.extend(
            [
                "## Boundary Building Audit",
                "",
                "This section uses two related checks. First, it runs the historical building query literally at each barracks "
                "milestone timestamp. Second, it replays the latest building-tagged layer against each historical barracks boundary "
                "as a proof-of-concept for how a boundary-only workflow could have swept later-clarified buildings into the site list.",
                "",
            ]
        )
        for finding in building_audit_summary.get("findings", []):
            lines.append(f"- {finding}")
        if building_stage_rows:
            lines.extend(
                [
                    "",
                    "### Historical snapshot query",
                    "",
                    markdown_table(pd.DataFrame(building_stage_rows)),
                    "",
                ]
            )
        for finding in building_audit_summary.get("replay_findings", []):
            lines.append(f"- {finding}")
        if replay_stage_rows:
            lines.extend(
                [
                    "",
                    "### Latest-building replay against each boundary stage",
                    "",
                    markdown_table(pd.DataFrame(replay_stage_rows)),
                    "",
                ]
            )
        lines.extend(
            [
                f"![Boundary building stages]({repo_relative(output_dir / 'site_boundary_building_stages.png', root_dir)})",
                "",
                "Each panel replays the latest building-tagged layer against the barracks boundary at that milestone. Points are "
                "coloured by where those building centers sit relative to the later school, compound, and latest barracks polygons.",
                "",
                f"![Boundary building counts]({repo_relative(output_dir / 'site_boundary_building_counts.png', root_dir)})",
                "",
                "The stacked counts quantify how the boundary revision changes which later-known buildings would be swept into the "
                "site list, and whether those captured buildings are generic `building=*` features or explicitly school- / "
                "military-related tags.",
                "",
            ]
        )

    # Local context section (if available)
    if local_context_summary is not None:
        lc = local_context_summary
        pre_total = lc["prestrike"]["total_features"]
        cur_total = lc["current"]["total_features"]
        pre_civ = lc["prestrike"]["by_category"].get("civilian_sensitive", 0)
        cur_civ = lc["current"]["by_category"].get("civilian_sensitive", 0)
        pre_mil = lc["prestrike"]["by_category"].get("military_security", 0)
        cur_mil = lc["current"]["by_category"].get("military_security", 0)
        max_r = max(lc["radii_m"])
        center = lc["center"]
        pre_query_description = lc.get("query_description_prestrike", local_context_prestrike_descriptor())
        pre_query_date_label = format_date(lc["query_date_prestrike"])

        cde_answer = (
            f"**Yes**: {pre_civ} civilian-sensitive feature(s) were present in the pre-strike OSM record within {max_r}m."
            if pre_civ > 0
            else f"**No**: zero civilian-sensitive features were present in the pre-strike OSM record within {max_r}m. The current record now shows {cur_civ}."
        )

        lines.extend([
            "## Local OSM Context and CDE-Relevant Information Environment",
            "",
            f"This section reconstructs the local OpenStreetMap information environment within {max_r}m of the analysis centroid "
            f"({center['lat']:.5f}, {center['lon']:.5f}) to test what a collateral damage estimation (CDE) process relying on OSM "
            f"data might have seen from a historical snapshot taken {pre_query_description} ({pre_query_date_label}) and from the current record.",
            "",
            f"- **Historical query date**: {lc['query_date_prestrike']} ({pre_query_description})",
            f"- **Strike date**: {lc['strike_date']}",
            f"- **Pre-strike features found**: {pre_total} (of which {pre_civ} civilian-sensitive, {pre_mil} military/security)",
            f"- **Current features found**: {cur_total} (of which {cur_civ} civilian-sensitive, {cur_mil} military/security)",
            f"- **Features added after strike**: {lc['comparison'].get('added', 0)}",
            f"- **Features removed after strike**: {lc['comparison'].get('removed', 0)}",
            "",
            f"**Would a pre-strike CDE query have flagged civilian-sensitive features nearby?** {cde_answer}",
            "",
            f"![Pre-strike local context]({repo_relative(output_dir / 'local_context_prestrike.png', root_dir)})",
            "",
            f"This map shows the {pre_total} OSM features within {max_r}m of the analysis centroid as recorded {pre_query_description} "
            f"on {pre_query_date_label}, coloured by CDE category. Concentric circles mark the 50m, 100m, 250m, and 500m radius bands.",
            "",
            f"![Current local context]({repo_relative(output_dir / 'local_context_current.png', root_dir)})",
            "",
            f"The same view using the current OSM record, showing {cur_total} features. Comparing this current snapshot against the "
            f"{pre_query_date_label} historical view reveals how the local information environment has changed since the strike.",
            "",
            f"![Local context comparison]({repo_relative(output_dir / 'local_context_comparison.png', root_dir)})",
            "",
            "This comparison panel combines side-by-side maps with a feature density chart and CDE summary. "
            f"The density chart shows feature counts by category for the {pre_query_date_label} historical snapshot versus current, while the summary panel "
            "directly addresses whether the pre-strike OSM record contained enough information to flag civilian presence.",
            "",
        ])

        # Named features table
        pre_named = lc["prestrike"].get("named_features", [])
        cur_named = lc["current"].get("named_features", [])
        if pre_named or cur_named:
            lines.append("### Named features near the site")
            lines.append("")
            all_named = {}
            for f in pre_named:
                all_named[f["name"]] = {"pre": f"{f['category']} ({f['distance_m']:.0f}m)", "cur": ""}
            for f in cur_named:
                if f["name"] in all_named:
                    all_named[f["name"]]["cur"] = f"{f['category']} ({f['distance_m']:.0f}m)"
                else:
                    all_named[f["name"]] = {"pre": "", "cur": f"{f['category']} ({f['distance_m']:.0f}m)"}
            named_rows = [{"Name": name, "Pre-strike": info["pre"], "Current": info["cur"]}
                          for name, info in sorted(all_named.items())]
            lines.append(markdown_table(pd.DataFrame(named_rows[:20])))
            lines.append("")

    lines.extend(
        [
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
            "- The boundary building audit uses OSM element centers for historical snapshot filtering; it does not reconstruct full historical building footprints.",
            "- Overpass mirror freshness can vary for broader current-state context queries, so local-context totals may differ across reruns.",
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


def build_summary_json(strike_timestamp, summary_by_way, all_milestones, key_findings, conflation, generated_files, root_dir, local_context_summary=None, building_audit_summary=None):
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

    result = {
        "project_title": PROJECT_TITLE,
        "strike_date": iso_timestamp(strike_timestamp),
        "generated_at": iso_timestamp(pd.Timestamp.now(tz="UTC")),
        "ways": ways,
        "key_findings": key_findings,
        "conflation_assessment": conflation,
        "generated_files": sorted(repo_relative(path, root_dir) for path in generated_files),
    }
    if local_context_summary is not None:
        result["local_context"] = local_context_summary
    if building_audit_summary is not None:
        result["boundary_building_audit"] = building_audit_summary
    return result


def write_results_txt(output_path, strike_timestamp, summary_df, milestone_df, combined_df, key_findings, summary_by_way, conflation, changeset_patterns=None, local_context_summary=None, building_audit_summary=None):
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
    lines.extend(["", "CONFLATION RISK", "-" * 100, f"Overall rating: {conflation['overall_rating']} (score: {conflation['score']}/100)"])
    for indicator in conflation["indicators"]:
        lines.append(f"- [{indicator['status'].upper()}] {indicator['label']}: {indicator['detail']}")

    # Spatial metrics
    lines.extend(["", "SPATIAL METRICS", "-" * 100])
    if conflation.get("school_area_sq_m") is not None:
        lines.append(f"- School polygon area (latest): {conflation['school_area_sq_m']:,.0f} sq m")
    if conflation.get("barracks_pre_area_sq_m") is not None:
        lines.append(f"- Pre-strike barracks area: {conflation['barracks_pre_area_sq_m']:,.0f} sq m")
    if conflation.get("school_in_barracks_pre_fraction") is not None:
        lines.append(f"- School vertices inside pre-strike barracks: {int(conflation['school_in_barracks_pre_fraction'] * 100)}%")
    if conflation.get("latest_school_compound_distance_m") is not None:
        lines.append(f"- School-to-compound minimum vertex distance: {conflation['latest_school_compound_distance_m']:.1f} m")
    if conflation.get("latest_school_barracks_distance_m") is not None:
        lines.append(f"- School-to-barracks minimum vertex distance: {conflation['latest_school_barracks_distance_m']:.1f} m")
    for key, count in conflation.get("shared_nodes", {}).items():
        lines.append(f"- Shared boundary nodes ({key.replace('_', '-')}): {count}")

    # Local context
    if local_context_summary is not None:
        lc = local_context_summary
        lines.extend(["", "LOCAL OSM CONTEXT (CDE INFORMATION ENVIRONMENT)", "-" * 100])
        lines.append(f"- Analysis centroid: {lc['center']['lat']:.5f}, {lc['center']['lon']:.5f}")
        lines.append(
            f"- Historical query date: {lc['query_date_prestrike']} "
            f"({lc.get('query_description_prestrike', local_context_prestrike_descriptor())})"
        )
        lines.append(f"- Strike date: {lc['strike_date']}")
        lines.append(f"- Pre-strike features within {max(lc['radii_m'])}m: {lc['prestrike']['total_features']}")
        lines.append(f"- Current features within {max(lc['radii_m'])}m: {lc['current']['total_features']}")
        lines.append(f"- Features added after strike: {lc['comparison'].get('added', 0)}")
        lines.append(f"- Features removed after strike: {lc['comparison'].get('removed', 0)}")
        lines.append("")
        lines.append("  Pre-strike features by category:")
        for cat in CDE_CATEGORY_ORDER:
            count = lc["prestrike"]["by_category"].get(cat, 0)
            if count > 0:
                lines.append(f"    {CDE_CATEGORY_LABELS.get(cat, cat)}: {count}")
        lines.append("  Current features by category:")
        for cat in CDE_CATEGORY_ORDER:
            count = lc["current"]["by_category"].get(cat, 0)
            if count > 0:
                lines.append(f"    {CDE_CATEGORY_LABELS.get(cat, cat)}: {count}")
        pre_civ = lc["prestrike"]["by_category"].get("civilian_sensitive", 0)
        if pre_civ > 0:
            lines.append(f"  [RISK] Pre-strike OSM record DID contain {pre_civ} civilian-sensitive feature(s).")
        else:
            lines.append(f"  [RISK] Pre-strike OSM record contained ZERO civilian-sensitive features.")

    if building_audit_summary is not None:
        lines.extend(["", "BOUNDARY BUILDING AUDIT", "-" * 100])
        lines.append(f"- Site way: {building_audit_summary['site_label']} ({building_audit_summary['site_way_id']})")
        lines.append(
            f"- Inclusion rule: {building_audit_summary['method']['inclusion_rule']}"
        )
        lines.append(
            f"- Later assignment rule: {building_audit_summary['method']['later_assignment_rule']}"
        )
        lines.append(
            f"- Unique building elements returned across all stages: {building_audit_summary.get('unique_building_elements', 0)}"
        )
        if building_audit_summary.get("replay_unique_building_elements") is not None:
            lines.append(
                f"- Unique building elements in latest-building replay: {building_audit_summary.get('replay_unique_building_elements', 0)}"
            )
        for finding in building_audit_summary.get("findings", []):
            lines.append(f"- {finding}")
        if building_audit_summary.get("historical_snapshot_stages", building_audit_summary.get("stages")):
            lines.append("")
            lines.append("  Historical stage summary:")
            for row in building_audit_summary.get("historical_snapshot_stages", building_audit_summary.get("stages", [])):
                if row.get("query_failed"):
                    lines.append(f"    {row['stage_label']}: snapshot unavailable ({compact_text(row.get('query_error', ''), 90)})")
                else:
                    lines.append(
                        "    "
                        f"{row['stage_label']}: {int(row['building_count'])} building(s), "
                        f"{int(row['later_school_polygon_count'])} later school, "
                        f"{int(row['later_compound_polygon_count'])} later compound, "
                        f"{int(row['latest_barracks_only_count'])} barracks-only, "
                        f"{int(row['outside_latest_barracks_count'])} outside latest barracks"
                    )
        if building_audit_summary.get("replay_findings"):
            lines.append("")
            lines.append("  Latest-building replay findings:")
            for finding in building_audit_summary.get("replay_findings", []):
                lines.append(f"    - {finding}")
        if building_audit_summary.get("replay_stages"):
            lines.append("")
            lines.append("  Replay stage summary:")
            for row in building_audit_summary["replay_stages"]:
                if row.get("query_failed"):
                    lines.append(f"    {row['stage_label']}: replay unavailable ({compact_text(row.get('query_error', ''), 90)})")
                else:
                    lines.append(
                        "    "
                        f"{row['stage_label']}: {int(row['building_count'])} captured building(s), "
                        f"{int(row['later_school_polygon_count'])} later school, "
                        f"{int(row['later_compound_polygon_count'])} later compound, "
                        f"{int(row['latest_barracks_only_count'])} barracks-only, "
                        f"{int(row['outside_latest_barracks_count'])} outside latest barracks"
                    )

    # Changeset patterns
    if changeset_patterns:
        lines.extend(["", "MULTI-WAY CHANGESETS (same editor, same changeset)", "-" * 100])
        for cs_id, edits in sorted(changeset_patterns.items()):
            user = edits[0]["user"]
            ts = format_timestamp(edits[0]["timestamp"])
            way_list = ", ".join(f"{way_label(e['way_id'])} v{e['version']}" for e in edits)
            lines.append(f"  Changeset {cs_id} by {user} at {ts}: {way_list}")

    lines.extend(["", "WAY NARRATIVES", "-" * 100])
    for way_id in WAY_IDS:
        lines.append(f"{way_label(way_id)} ({way_id})")
        lines.append(textwrap.fill(build_way_narrative(summary_by_way[way_id], strike_timestamp), width=100))
        lines.append("")
    lines.extend(["MILESTONE COMPARISON", "-" * 100, milestone_df.to_string(index=False), "", "SUMMARY TABLE", "-" * 100, summary_df.to_string(index=False), "", "COMBINED EDIT TABLE", "-" * 100, combined_df[["way_id", "way_label", "version", "timestamp", "changeset", "user", "node_count", "closed_way", "perimeter_m", "area_sq_m", "edit_type", "tag_changes", "geometry_reconstruction"]].to_string(index=False), ""])
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

    generated_files.extend(generate_state_maps(all_geometries, all_milestones, strike_timestamp, output_dir))

    before_after_path = output_dir / "before_after_comparison.png"
    create_before_after_comparison(all_geometries, all_milestones, strike_timestamp, before_after_path)
    generated_files.append(before_after_path)

    changeset_patterns = analyze_changeset_patterns(all_histories)

    conflation = build_conflation_assessment(all_milestones, all_geometries, all_histories)
    conflation_plot = output_dir / "conflation_risk.png"
    plot_conflation_risk(conflation, conflation_plot)
    generated_files.append(conflation_plot)

    print(f"\n=== BOUNDARY BUILDING AUDIT ===")
    building_audit = collect_building_boundary_audit(all_geometries, all_milestones, output_dir)
    generated_files.extend(building_audit["generated_files"])

    key_findings = generate_key_findings(summary_by_way, conflation, strike_timestamp, building_audit["summary"])

    # --- Local context collection ---
    local_context_summary = None
    centroid_lat, centroid_lon = compute_all_geometries_centroid(all_geometries, all_milestones)
    if centroid_lat is not None:
        print(f"\n=== LOCAL CONTEXT COLLECTION ===")
        print(f"Analysis centroid: {centroid_lat:.6f}, {centroid_lon:.6f}")

        pre_strike_query_date = local_context_prestrike_timestamp(strike_timestamp)
        pre_strike_description = local_context_prestrike_descriptor()
        pre_strike_iso = pre_strike_query_date.strftime("%Y-%m-%dT%H:%M:%SZ")

        print(f"Querying Overpass API for historical state {pre_strike_description} ({pre_strike_iso})...")
        query_pre = build_overpass_query(centroid_lat, centroid_lon,
                                         LOCAL_CONTEXT_QUERY_RADIUS_M,
                                         date_iso=pre_strike_iso)
        response_pre = fetch_overpass(query_pre)
        features_pre = extract_nearby_features(response_pre, centroid_lat, centroid_lon,
                                               LOCAL_CONTEXT_RADII_M)
        df_pre = build_nearby_features_df(features_pre)

        time.sleep(2)  # Rate-limit courtesy for Overpass API

        print("Querying Overpass API for current state...")
        query_current = build_overpass_query(centroid_lat, centroid_lon,
                                             LOCAL_CONTEXT_QUERY_RADIUS_M)
        response_current = fetch_overpass(query_current)
        features_current = extract_nearby_features(response_current, centroid_lat, centroid_lon,
                                                   LOCAL_CONTEXT_RADII_M)
        df_current = build_nearby_features_df(features_current)

        # Save CSVs
        pre_csv = output_dir / "nearby_features_prestrike.csv"
        df_pre.to_csv(pre_csv, index=False)
        generated_files.append(pre_csv)

        current_csv = output_dir / "nearby_features_current.csv"
        df_current.to_csv(current_csv, index=False)
        generated_files.append(current_csv)

        df_comparison = compare_prestrike_current(df_pre, df_current)
        comparison_csv = output_dir / "nearby_features_comparison.csv"
        df_comparison.to_csv(comparison_csv, index=False)
        generated_files.append(comparison_csv)

        # Build summary
        local_context_summary = build_local_context_summary(
            df_pre, df_current, LOCAL_CONTEXT_RADII_M,
            centroid_lat, centroid_lon,
            pre_strike_iso, iso_timestamp(strike_timestamp),
            pre_strike_description, LOCAL_CONTEXT_PRESTRIKE_OFFSET_DAYS,
        )
        local_summary_path = output_dir / "nearby_features_summary.json"
        local_summary_path.write_text(
            json.dumps(local_context_summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        generated_files.append(local_summary_path)

        # Collect latest way geometries for overlay
        latest_way_coords = {}
        for way_id in WAY_IDS:
            latest = all_milestones[way_id]["latest"]
            if latest is not None:
                coords = all_geometries[way_id].get(int(latest["version"]), [])
                if len(coords) >= 2:
                    latest_way_coords[way_id] = coords

        pre_date_label = format_date(pre_strike_query_date)
        strike_date_label = format_date(strike_timestamp)
        pre_context_label = pre_strike_description.capitalize()

        pre_map_path = output_dir / "local_context_prestrike.png"
        plot_local_context_map(df_pre, centroid_lat, centroid_lon, LOCAL_CONTEXT_RADII_M,
                               f"Local OSM context: {pre_strike_description} ({pre_date_label})",
                               pre_map_path, way_overlays=latest_way_coords)
        generated_files.append(pre_map_path)

        current_map_path = output_dir / "local_context_current.png"
        plot_local_context_map(df_current, centroid_lat, centroid_lon, LOCAL_CONTEXT_RADII_M,
                               "Local OSM context: current",
                               current_map_path, way_overlays=latest_way_coords)
        generated_files.append(current_map_path)

        comparison_map_path = output_dir / "local_context_comparison.png"
        plot_local_context_comparison(df_pre, df_current, centroid_lat, centroid_lon,
                                      LOCAL_CONTEXT_RADII_M, comparison_map_path,
                                      way_overlays=latest_way_coords,
                                      pre_date_label=pre_date_label,
                                      strike_date_label=strike_date_label,
                                      pre_context_label=pre_context_label)
        generated_files.append(comparison_map_path)

        print(f"Local context: {len(df_pre)} pre-strike features, {len(df_current)} current features")
    else:
        print("Warning: could not compute centroid for local context collection.")

    results_txt = output_dir / "results.txt"
    write_results_txt(results_txt, strike_timestamp, summary_df, milestone_df, combined_df, key_findings, summary_by_way, conflation, changeset_patterns, local_context_summary, building_audit["summary"])
    generated_files.append(results_txt)

    readme_path = generate_readme(root_dir, output_dir, strike_timestamp, summary_df, summary_by_way, key_findings, conflation, generated_files, local_context_summary, building_audit["summary"])
    generated_files.append(readme_path)

    summary_json = output_dir / "summary.json"
    generated_files.append(summary_json)
    summary_json.write_text(json.dumps(build_summary_json(strike_timestamp, summary_by_way, all_milestones, key_findings, conflation, generated_files, root_dir, local_context_summary, building_audit["summary"]), ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== KEY FINDINGS ===")
    for finding in key_findings:
        print(f"- {finding}")
    print("\n=== SUMMARY TABLE ===")
    with pd.option_context("display.max_colwidth", 120, "display.width", 260):
        print(summary_df.to_string(index=False))
    print("\nSaved files:")
    for path in sorted(set(generated_files)):
        print(f"- {path}")
