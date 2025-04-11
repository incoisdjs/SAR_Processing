import os
from datetime import date, timedelta
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape
import json
from dotenv import load_dotenv
import streamlit as st
import folium
from streamlit_folium import st_folium
import subprocess
import threading
import queue
import time

# Load environment variables
load_dotenv()

if 'download_states' not in st.session_state:
    st.session_state.download_states = {}

def get_keycloak_token(username: str, password: str) -> str:
    """
    Get access token from Copernicus using username and password
    """
    data = {
        "client_id": "cdse-public",
        "username": username,
        "password": password,
        "grant_type": "password",
    }
    try:
        r = requests.post(
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            data=data,
        )
        r.raise_for_status()
        return r.json()["access_token"]
    except Exception as e:
        st.error(f"Error acquiring token: {str(e)}")
        if hasattr(r, 'json'):
            try:
                st.error(f"Response: {r.json()}")
            except:
                st.error(f"Response: {r.text}")
        return None

def search_products(token: str, bbox: str, collection: str, start_date: str, end_date: str, result_limit: int = 1000):
    """
    Search for products in the Copernicus catalog
    """
    url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
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
        st.error(f"Error searching catalog: {str(e)}")
        return None

def download_worker(product_id, token, product_name, output_dir, progress_queue):
    """Worker function to handle download in a separate process"""
    try:
        output_path = os.path.join(output_dir, f"{product_name}.zip")
        session = requests.Session()
        session.headers.update({"Authorization": f"Bearer {token}"})
        
        url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"
        
        # Follow redirects
        response = session.get(url, allow_redirects=False)
        while response.status_code in (301, 302, 303, 307):
            url = response.headers["Location"]
            response = session.get(url, allow_redirects=False)
        
        # Get file size
        file_response = session.get(url, stream=True, verify=False)
        total_size = int(file_response.headers.get('content-length', 0))
        
        # Download with progress
        downloaded = 0
        with open(output_path, "wb") as f:
            for chunk in file_response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    progress = (downloaded / total_size) * 100 if total_size > 0 else 0
                    progress_queue.put(('progress', progress, downloaded, total_size))
        
        progress_queue.put(('complete', output_path))
    except Exception as e:
        progress_queue.put(('error', str(e)))

def create_map(center_lat=40.7, center_lon=-73.9, zoom=10):
    """Create a Folium map with a rectangle drawing tool"""
    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom)
    folium.Rectangle(
        bounds=[[40.4, -74.3], [41.0, -73.5]],  # Default NYC bounds
        color='#ff7800',
        fill=True,
        fill_color='#ffff00',
        fill_opacity=0.2,
        popup='Area of Interest'
    ).add_to(m)
    return m

def download_callback(product_id, token, product_name):
    """Callback function for download button"""
    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{product_name}.zip")
    
    with st.spinner(f"Downloading {product_name}..."):
        if download_product(product_id, token, output_path):
            st.session_state[f"download_complete_{product_id}"] = True
            st.session_state[f"download_path_{product_id}"] = output_path
            st.success(f"Successfully downloaded {product_name}")
        else:
            st.session_state[f"download_error_{product_id}"] = True
            st.error(f"Failed to download {product_name}")

def main():
    st.title("Sentinel Data Downloader")
    st.markdown("""
    This app allows you to search and download Sentinel satellite data from Copernicus.
    Please provide your credentials and select your area of interest.
    """)
    
    # Sidebar for credentials
    with st.sidebar:
        st.header("Credentials")
        username = st.text_input("Copernicus Username")
        password = st.text_input("Copernicus Password", type="password")
        
        st.header("Search Parameters")
        collection = st.selectbox(
            "Select Collection",
            ["SENTINEL-1", "SENTINEL-2", "SENTINEL-3", "SENTINEL-5P"]
        )
        
        # Date range selector
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(
                "Start Date",
                value=date.today() - timedelta(days=30)
            )
        with col2:
            end_date = st.date_input(
                "End Date",
                value=date.today()
            )
        
        result_limit = st.number_input(
            "Maximum Results",
            min_value=1,
            max_value=1000,
            value=100
        )
    
    # Main content area
    st.header("Area of Interest")
    
    # Create map
    m = create_map()
    map_data = st_folium(m, width=700, height=500)
    
    # Get bounding box from map
    if map_data.get('last_active_drawing'):
        bounds = map_data['last_active_drawing']['geometry']['coordinates'][0]
        bbox = f"POLYGON(({bounds[0][0]} {bounds[0][1]}, {bounds[1][0]} {bounds[1][1]}, {bounds[2][0]} {bounds[2][1]}, {bounds[3][0]} {bounds[3][1]}, {bounds[0][0]} {bounds[0][1]}))"
    else:
        # Default NYC bounds
        bbox = "POLYGON((-74.3 40.4, -74.3 41.0, -73.5 41.0, -73.5 40.4, -74.3 40.4))"
    
    # Search button
    if st.button("Search Products"):
        if not username or not password:
            st.error("Please provide your Copernicus credentials")
            st.stop()
        
        with st.spinner("Authenticating..."):
            token = get_keycloak_token(username, password)
            if not token:
                st.error("Authentication failed")
                st.stop()
        
        with st.spinner("Searching products..."):
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
        
        # Display results
        products = results['value']
        total_count = results.get('@odata.count', len(products))
        
        st.success(f"Found {total_count} products, displaying {len(products)}")
        
        # Convert to DataFrame
        df = pd.DataFrame.from_dict(products)
        
        if len(products) > 0:
            # Add geometry if available
            if 'GeoFootprint' in df.columns:
                df['geometry'] = df['GeoFootprint'].apply(shape)
                gdf = gpd.GeoDataFrame(df).set_geometry('geometry')
                
                # Filter out L1C products if needed
                if collection == "SENTINEL-2":
                    gdf = gdf[~gdf['Name'].str.contains('L1C')]
                
                # Display products in tabs
                tabs = st.tabs([f"Product {i+1}" for i in range(len(gdf))])
                
                # Show download status for each product
                for i, tab in enumerate(tabs):
                    with tab:
                        row = gdf.iloc[i]
                        st.subheader(row['Name'])
                        
                        # Display product info
                        st.json({
                            'ID': row['Id'],
                            'Size': f"{row.get('ContentLength', 0) / (1024*1024):.2f} MB",
                            'Date': row.get('ContentDate', {}).get('Start'),
                            'Cloud Cover': row.get('CloudCover', 'N/A')
                        })
                        
                        # Initialize download state if not exists
                        if row['Id'] not in st.session_state.download_states:
                            st.session_state.download_states[row['Id']] = {
                                'complete': False,
                                'error': False,
                                'started': False,
                                'progress': 0
                            }
                        
                        download_state = st.session_state.download_states[row['Id']]
                        
                        # Add download button if not already downloading
                        if not download_state['started'] and not download_state['complete']:
                            output_dir = st.text_input(
                                "Select download directory",
                                value=os.path.join(os.getcwd(), "downloads"),
                                key=f"dir_{row['Id']}"
                            )
                            
                            if st.button("Download", key=f"download_{row['Id']}"):
                                if not os.path.exists(output_dir):
                                    os.makedirs(output_dir)
                                
                                # Start download in a separate process
                                download_state['started'] = True
                                process = subprocess.Popen(
                                    [
                                        'python', 'download_handler.py',
                                        row['Id'],
                                        token,
                                        row['Name'],
                                        output_dir
                                    ],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True
                                )
                                
                                # Store process in session state
                                st.session_state[f"process_{row['Id']}"] = process
                        
                        # Check download progress
                        if download_state['started']:
                            process = st.session_state.get(f"process_{row['Id']}")
                            if process:
                                # Read output from process
                                while True:
                                    line = process.stdout.readline()
                                    if not line:
                                        break
                                    try:
                                        status = json.loads(line)
                                        if status['status'] == 'progress':
                                            download_state['progress'] = status['progress']
                                            st.progress(status['progress'] / 100)
                                            st.write(f"Downloaded: {status['downloaded'] / (1024*1024):.2f} MB / {status['total_size'] / (1024*1024):.2f} MB")
                                        elif status['status'] == 'complete':
                                            download_state['complete'] = True
                                            download_state['path'] = status['path']
                                            st.success("Download complete!")
                                        elif status['status'] == 'error':
                                            download_state['error'] = True
                                            st.error(f"Download failed: {status['error']}")
                                    except json.JSONDecodeError:
                                        continue
                        
                        # Show download status
                        if download_state['complete']:
                            if 'path' in download_state:
                                file_path = download_state['path']
                                if os.path.exists(file_path):
                                    st.success(f"File saved to: {file_path}")
                                else:
                                    st.error("File not found. Please try downloading again.")
                        elif download_state['error']:
                            st.error("Download failed. Please try again.")
                            if st.button("Retry Download", key=f"retry_{row['Id']}"):
                                download_state['error'] = False
                                download_state['started'] = False
                                download_state['progress'] = 0
                        elif download_state['started']:
                            st.info("Download in progress...")
                            st.progress(download_state['progress'] / 100)

if __name__ == "__main__":
    main() 