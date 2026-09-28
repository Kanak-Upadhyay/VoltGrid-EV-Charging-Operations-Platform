import math
from decimal import Decimal


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    delta_lat = radius_km / 111.0
    cos_lat = math.cos(math.radians(lat))
    # Guard the poles so a search there does not divide by zero.
    delta_lng = radius_km / (111.0 * max(abs(cos_lat), 0.01))
    return lat - delta_lat, lat + delta_lat, lng - delta_lng, lng + delta_lng


def money_km(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))
