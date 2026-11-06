from collections.abc import Mapping, Sequence

import requests


GOOGLE_GEOLOCATION_URL = "https://www.googleapis.com/geolocation/v1/geolocate"


class GeolocationError(RuntimeError):
    pass


def locate_wifi_access_points(
    access_points: Sequence[Mapping[str, object]], api_key: str
) -> dict[str, float]:
    if not api_key:
        raise GeolocationError("Google Geolocation API key is not configured.")
    if not access_points:
        raise GeolocationError("At least one Wi-Fi access point is required.")

    payload = {"wifiAccessPoints": list(access_points)}
    try:
        response = requests.post(
            GOOGLE_GEOLOCATION_URL,
            params={"key": api_key},
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        location = data["location"]
        latitude = float(location["lat"])
        longitude = float(location["lng"])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Coordinates are outside valid ranges.")
        return {"lat": latitude, "lng": longitude}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise GeolocationError("Google Geolocation request failed.") from exc
