import requests
import json

base_url = "https://api.daac.asf.alaska.edu/services/search/param"

def fetch_data(platform, latitude, longitude, start_date, end_date, result_limit):
    params = {
        "platform": platform,
        "intersectsWith": f"POINT({longitude} {latitude})",  # WKT format for location
        "start": start_date,
        "end": end_date,
        "output": "geojson",
        "maxResults": result_limit  # Use the result_limit parameter here
    }

    response = requests.get(base_url, params=params)

    if response.status_code == 200:
        return response.json()
    else:
        return None 