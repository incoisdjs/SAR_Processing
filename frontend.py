import streamlit as st
import requests
import os
import json
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape
from datetime import date, timedelta
import folium
from streamlit_folium import st_folium
import time
import asyncio
import aiohttp
import io

# Copernicus API endpoints
COPERNICUS_AUTH_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
COPERNICUS_CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

def get_keycloak_token(username: str, password: str) -> str:
    """Get access token from Copernicus using username and password"""
    data = {
        "client_id": "cdse-public",
        "username": username,
        "password": password,
        "grant_type": "password",
    }
    try:
        r = requests.post(COPERNICUS_AUTH_URL, data=data)
        r.raise_for_status()
        return r.json()["access_token"]
    except Exception as e:
        st.error(f"Authentication failed: {str(e)}")
        return None

def search_products(token: str, bbox: str, collection: str, start_date: str, end_date: str, result_limit: int = 1000):
    """Search for products in the Copernicus catalog"""
    url = COPERNICUS_CATALOG_URL
    params = {
        "$filter": f"Collection/Name eq '{collection}' and OData.CSC.Intersects(area=geography'SRID=4326;{bbox}') and ContentDate/Start gt {start_date}T00:00:00.000Z and ContentDate/Start lt {end_date}T23:59:59.999Z",
        "$count": "True",
        "$top": result_limit
    }
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Search failed: {str(e)}")
        return None

async def download_product(session, product_id: str, token: str, product_name: str, output_dir: str, progress_placeholder, status_placeholder):
    """Download a product with progress tracking"""
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        output_path = os.path.join(output_dir, f"{product_name}.zip")
        headers = {"Authorization": f"Bearer {token}"}
        
        # Get the download URL
        url = f"{COPERNICUS_CATALOG_URL}({product_id})/$value"
        
        # Follow redirects
        async with session.get(url, headers=headers, allow_redirects=False) as response:
            while response.status in (301, 302, 303, 307):
                url = response.headers["Location"]
                response = await session.get(url, headers=headers, allow_redirects=False)
        
        # Get file size
        async with session.get(url, headers=headers) as response:
            total_size = int(response.headers.get('content-length', 0))
            status_placeholder.write(f"Total size: {total_size / (1024*1024):.2f} MB")
            
            # Download with progress
            downloaded = 0
            with open(output_path, "wb") as f:
                async for chunk in response.content.iter_chunked(8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        progress = (downloaded / total_size) * 100 if total_size > 0 else 0
                        progress_placeholder.progress(progress / 100)
                        status_placeholder.write(
                            f"Downloading: {downloaded / (1024*1024):.2f} MB / "
                            f"{total_size / (1024*1024):.2f} MB ({progress:.1f}%)"
                        )
            
            status_placeholder.success(f"Download complete! File saved to: {output_path}")
            return output_path
            
    except Exception as e:
        status_placeholder.error(f"Download failed: {str(e)}")
        return None

def create_map(center_lat=40.7, center_lon=-73.9, zoom=10):
    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom)
    folium.Rectangle(
        bounds=[[40.4, -74.3], [41.0, -73.5]],
        color='#ff7800',
        fill=True,
        fill_color='#ffff00',
        fill_opacity=0.2,
        popup='Area of Interest'
    ).add_to(m)
    return m

def main():
    st.title("Sentinel Data Downloader")

    with st.sidebar:
        st.header("Credentials")
        username = st.text_input("Copernicus Username", key="username")
        password = st.text_input("Copernicus Password", type="password", key="password")

        st.header("Search Parameters")
        collection = st.selectbox(
            "Select Collection",
            ["SENTINEL-1", "SENTINEL-2", "SENTINEL-3", "SENTINEL-5P"],
            key="collection"
        )

        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", value=date.today() - timedelta(days=30), key="start_date")
        with col2:
            end_date = st.date_input("End Date", value=date.today(), key="end_date")

        result_limit = st.number_input("Maximum Results", min_value=1, max_value=1000, value=100, key="limit")

    st.header("Area of Interest")
    
    # Create two columns for map and manual input
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # Create map
        m = create_map()
        map_data = st_folium(m, width=700, height=500)
    
    with col2:
        st.subheader("Manual Bounding Box")
        st.markdown("Enter coordinates in decimal degrees:")
        
        # Create input fields for bounding box
        bbox_col1, bbox_col2 = st.columns(2)
        
        with bbox_col1:
            min_lat = st.number_input("Min Latitude", value=40.4, format="%.4f", key="min_lat")
            min_lon = st.number_input("Min Longitude", value=-74.3, format="%.4f", key="min_lon")
        
        with bbox_col2:
            max_lat = st.number_input("Max Latitude", value=41.0, format="%.4f", key="max_lat")
            max_lon = st.number_input("Max Longitude", value=-73.5, format="%.4f", key="max_lon")
        
        # Add a button to update map with manual coordinates
        if st.button("Update Map with Coordinates"):
            # Update the map with new bounds
            m = folium.Map(location=[(min_lat + max_lat)/2, (min_lon + max_lon)/2], zoom_start=10)
            folium.Rectangle(
                bounds=[[min_lat, min_lon], [max_lat, max_lon]],
                color='#ff7800',
                fill=True,
                fill_color='#ff7800',
                fill_opacity=0.2
            ).add_to(m)
            map_data = st_folium(m, width=700, height=500, key="updated_map")
    
    # Get bounding box from either map or manual input
    if map_data.get('last_active_drawing'):
        bounds = map_data['last_active_drawing']['geometry']['coordinates'][0]
        bbox = f"POLYGON(({bounds[0][0]} {bounds[0][1]}, {bounds[1][0]} {bounds[1][1]}, {bounds[2][0]} {bounds[2][1]}, {bounds[3][0]} {bounds[3][1]}, {bounds[0][0]} {bounds[0][1]}))"
    else:
        # Use manual input coordinates
        bbox = f"POLYGON(({min_lon} {min_lat}, {min_lon} {max_lat}, {max_lon} {max_lat}, {max_lon} {min_lat}, {min_lon} {min_lat}))"
    
    # Show current bounding box
    st.markdown("### Current Bounding Box")
    st.code(bbox, language="text")

    if 'search_results' not in st.session_state:
        st.session_state.search_results = None
    if 'token' not in st.session_state:
        st.session_state.token = None

    if st.button("Search Products"):
        if not username or not password:
            st.error("Please provide your Copernicus credentials")
            st.stop()

        try:
            # Get authentication token
            token = get_keycloak_token(username, password)
            if not token:
                st.stop()
            
            st.session_state.token = token
            
            # Search for products
            results = search_products(
                token=token,
                bbox=bbox,
                collection=collection,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                result_limit=result_limit
            )
            
            if not results or 'value' not in results:
                st.error("No products found matching your criteria")
                st.stop()

            st.session_state.search_results = results['value']
            st.success(f"Found {len(st.session_state.search_results)} products")

        except Exception as e:
            st.error(f"Error: {str(e)}")

    if st.session_state.search_results:
        df = pd.DataFrame.from_dict(st.session_state.search_results)

        if 'GeoFootprint' in df.columns:
            df['geometry'] = df['GeoFootprint'].apply(shape)
            gdf = gpd.GeoDataFrame(df).set_geometry('geometry')

            if collection == "SENTINEL-2":
                gdf = gdf[~gdf['Name'].str.contains('L1C')]

            tabs = st.tabs([f"Product {i+1}" for i in range(len(gdf))])

            for i, tab in enumerate(tabs):
                with tab:
                    row = gdf.iloc[i]
                    st.subheader(row['Name'])

                    st.json({
                        'ID': row['Id'],
                        'Size': f"{row.get('ContentLength', 0) / (1024*1024):.2f} MB",
                        'Date': row.get('ContentDate', {}).get('Start'),
                        'Cloud Cover': row.get('CloudCover', 'N/A')
                    })

                    with st.form(key=f"form_{row['Id']}"):
                        output_dir = st.text_input(
                            "Select download directory",
                            value=os.path.join(os.getcwd(), "downloads"),
                            key=f"dir_{row['Id']}"
                        )
                        submit = st.form_submit_button("Download")

                        if submit:
                            progress_placeholder = st.empty()
                            status_placeholder = st.empty()
                            
                            async def download():
                                async with aiohttp.ClientSession() as session:
                                    await download_product(
                                        session,
                                        row['Id'],
                                        st.session_state.token,
                                        row['Name'],
                                        output_dir,
                                        progress_placeholder,
                                        status_placeholder
                                    )
                            
                            asyncio.run(download())

if __name__ == "__main__":
    main()
