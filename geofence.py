import csv
import json
import os
from shapely.geometry import Point, Polygon

# Cache the polygons in memory
_fields_cache = {}

def _load_fields():
    global _fields_cache
    _fields_cache = {}
    csv_path = os.path.join(os.path.dirname(__file__), "fields.csv")
    if not os.path.exists(csv_path):
        return
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Field Name")
            coords_str = row.get("Coordinates")
            if name and coords_str:
                try:
                    # coords_str is like "[[lat, lon], [lat, lon], ...]"
                    coords = json.loads(coords_str)
                    # Shapely expects (lon, lat) for (x, y)
                    lon_lat_coords = [(c[1], c[0]) for c in coords]
                    polygon = Polygon(lon_lat_coords)
                    _fields_cache[name] = polygon
                except Exception as e:
                    print(f"Error parsing coordinates for field {name}: {e}")

def calculate_polygon_dunam_area(coords: list) -> float:
    """Calculate polygon area in Dunams (1 Dunam = 1000 sq meters) using spherical projection.

    Args:
        coords (list): List of [lat, lng] pairs.

    Returns:
        float: Area in Dunams rounded to 2 decimal places.
    """
    import math
    if not coords or len(coords) < 3:
        return 0.0
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    avg_lat = sum(lats) / len(lats)
    lat_meters = 111000.0
    lon_meters = 111000.0 * math.cos(math.radians(avg_lat))
    x = [lon * lon_meters for lon in lons]
    y = [lat * lat_meters for lat in lats]
    area_sq_m = 0.5 * abs(sum(x[i] * y[i - 1] - x[i - 1] * y[i] for i in range(len(coords))))
    return round(area_sq_m / 1000.0, 2)


def save_field_polygon(name: str, coords: list):
    """Save or update field polygon coordinates locally in fields.csv and update cache.

    Args:
        name (str): Field name.
        coords (list): List of [lat, lng] coordinate pairs.
    """
    csv_path = os.path.join(os.path.dirname(__file__), "fields.csv")
    rows = []
    header = ["Field Name", "Coordinates"]
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            first = next(reader, None)
            if first and first[0].strip() != "Field Name":
                rows.append(first)
            for r in reader:
                if r and r[0] != name:
                    rows.append(r)
    rows.append([name, json.dumps(coords)])
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    _load_fields()


def delete_field_polygon(name: str):
    """Delete a field polygon from local fields.csv storage.

    Args:
        name (str): Field name.
    """
    csv_path = os.path.join(os.path.dirname(__file__), "fields.csv")
    if not os.path.exists(csv_path):
        return
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for r in reader:
            if r and r[0] != name:
                rows.append(r)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if header:
            writer.writerow(header)
        writer.writerows(rows)
    _load_fields()


def get_fields_data():
    """Returns raw field data (name, points list, area in Dunams) for the frontend map."""
    csv_path = os.path.join(os.path.dirname(__file__), "fields.csv")
    fields = []
    if not os.path.exists(csv_path):
        return fields
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Field Name")
            coords_str = row.get("Coordinates")
            if name and coords_str:
                try:
                    coords = json.loads(coords_str)
                    fields.append({
                        "name": name,
                        "coordinates": coords,
                        "area_dunam": calculate_polygon_dunam_area(coords)
                    })
                except Exception:
                    pass
    return fields


def match_coordinate_to_field(lat: float, lon: float) -> str:
    """Returns the name of the field containing the (lat, lon) point, or Unknown."""
    if not _fields_cache:
        _load_fields()
    
    # User point (lon, lat)
    user_point = Point(lon, lat)
    
    for field_name, polygon in _fields_cache.items():
        if polygon.contains(user_point):
            return field_name
            
    return "Unknown/Mismatched"


# Load fields at module initialization
_load_fields()
