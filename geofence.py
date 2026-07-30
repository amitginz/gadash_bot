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

def get_fields_data():
    """Returns raw field data (name, points list) for the frontend map."""
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
                    fields.append({"name": name, "coordinates": coords})
                except:
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
