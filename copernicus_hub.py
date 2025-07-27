import streamlit as st
import pandas as pd
import geopandas as gpd
from datetime import date, datetime, timedelta
import asyncio
import aiohttp
import io
import os
import requests
from shapely.geometry import shape

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
        # Test basic internet connectivity first
        try:
            requests.get("https://www.google.com", timeout=5)
        except:
            st.error("No internet connection detected. Please check your network connection.")
            return None

        # Try to resolve the Copernicus domain
        try:
            requests.get("https://identity.dataspace.copernicus.eu", timeout=5)
        except:
            st.error("""
                Unable to connect to Copernicus servers. This could be due to:
                1. Network restrictions or firewall settings
                2. DNS resolution issues
                3. VPN requirements
                
                Please try:
                1. Checking your internet connection
                2. Using a different network
                3. Connecting to a VPN if required
                4. Contacting your network administrator
            """)
            return None

        # Attempt authentication
        r = requests.post(COPERNICUS_AUTH_URL, data=data, timeout=10)
        r.raise_for_status()
        return r.json()["access_token"]
    except requests.exceptions.ConnectionError:
        st.error("Connection Error: Unable to reach Copernicus servers. Please check your internet connection.")
        return None
    except requests.exceptions.Timeout:
        st.error("Connection Timeout: The request took too long. Please try again.")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Authentication Error: {str(e)}")
        return None
    except Exception as e:
        st.error(f"Unexpected Error: {str(e)}")
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
        # Ensure the output directory exists
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
                status_placeholder.info(f"Created directory: {output_dir}")
            except Exception as e:
                status_placeholder.error(f"Failed to create directory: {output_dir}. Error: {str(e)}")
                return None, False
        
        # Clean the product name to ensure it's a valid filename
        clean_name = "".join(c for c in product_name if c.isalnum() or c in (' ', '-', '_', '.')).strip()
        output_path = os.path.join(output_dir, f"{clean_name}.zip")
        
        # Check if file already exists
        if os.path.exists(output_path):
            status_placeholder.warning(f"File already exists at: {output_path}")
            return output_path, True
        
        # Check write permissions on output directory
        try:
            test_file = os.path.join(output_dir, ".write_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except Exception as e:
            status_placeholder.error(f"No write permission on directory: {output_dir}. Error: {str(e)}")
            return None, False
            
        headers = {"Authorization": f"Bearer {token}"}
        
        # Get the download URL
        url = f"{COPERNICUS_CATALOG_URL}({product_id})/$value"
        
        # Follow redirects
        async with session.get(url, headers=headers, allow_redirects=False) as response:
            while response.status in (301, 302, 303, 307):
                url = response.headers["Location"]
                response = await session.get(url, headers=headers, allow_redirects=False)
        
        # Get file size using HEAD request
        try:
            async with session.head(url, headers=headers) as response:
                total_size = int(response.headers.get('content-length', 0))
                status_placeholder.write(f"Total size: {total_size / (1024*1024):.2f} MB")
                
                # Check available disk space
                try:
                    import shutil
                    free_space = shutil.disk_usage(output_dir).free
                    if free_space < total_size * 1.2:  # Add 20% buffer
                        status_placeholder.error(f"Not enough disk space. Required: {total_size * 1.2 / (1024*1024*1024):.2f} GB, Available: {free_space / (1024*1024*1024):.2f} GB")
                        return None, False
                except Exception as e:
                    status_placeholder.warning(f"Could not check disk space: {str(e)}")
        except Exception as e:
            # If HEAD request fails, continue anyway and get size during download
            status_placeholder.warning(f"Couldn't determine file size in advance: {str(e)}")
            total_size = 0  # Will be updated during download if available
        
        # Create a temporary file for streaming
        temp_file_path = output_path + ".part"
        
        # Stream download directly to disk to handle large files
        try:
            # Create a BytesIO object for browser download (limited size)
            browser_data = io.BytesIO() if total_size < 200 * 1024 * 1024 and total_size > 0 else None  # Only store in memory if < 200 MB
            can_browser_download = browser_data is not None
            
            downloaded = 0
            async with session.get(url, headers=headers) as response:
                # If we couldn't get the size before, get it now
                if total_size == 0:
                    total_size = int(response.headers.get('content-length', 0))
                    status_placeholder.write(f"Total size: {total_size / (1024*1024):.2f} MB")
                    
                    # Update browser_data decision based on actual size
                    if total_size < 200 * 1024 * 1024 and total_size > 0:
                        browser_data = io.BytesIO()
                        can_browser_download = True
                    else:
                        browser_data = None
                        can_browser_download = False
                
                with open(temp_file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        if chunk:
                            # Write chunk to disk
                            f.write(chunk)
                            
                            # Also save to BytesIO if it's not too large
                            if can_browser_download:
                                browser_data.write(chunk)
                            
                            downloaded += len(chunk)
                            progress = (downloaded / total_size) * 100 if total_size > 0 else 0
                            
                            # Update progress less frequently to reduce UI load
                            if int(progress) % 2 == 0 or downloaded == total_size:
                                progress_placeholder.progress(progress / 100)
                                status_placeholder.write(
                                    f"Downloading: {downloaded / (1024*1024):.2f} MB / "
                                    f"{total_size / (1024*1024):.2f} MB ({progress:.1f}%)"
                                )
            
            # Rename temp file to final file
            os.rename(temp_file_path, output_path)
            
            # Prepare browser download if available
            if can_browser_download:
                browser_data.seek(0)
            
            status_placeholder.success(f"Download complete! File saved to: {output_path}")
            
            # Return both the file path and the BytesIO object (or None if too large for browser download)
            return (output_path, browser_data), False
        
        except Exception as e:
            # Clean up temp file if download failed
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except:
                    pass
            status_placeholder.error(f"Error downloading file: {str(e)}")
            return None, False
            
    except Exception as e:
        status_placeholder.error(f"Download failed: {str(e)}")
        return None, False

def render_copernicus_interface():
    """Render the complete Copernicus Hub interface"""
    st.title("Copernicus Hub")
    
    # Create two columns for main content and sidebar with adjusted widths
    main_col, sidebar_col = st.columns([2, 1])
    
    with sidebar_col:
        st.header("Credentials")
        with st.form(key="search_form"):
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
            
            search_submitted = st.form_submit_button("Search Products")
    
    with main_col:
        st.header("Area of Interest")
        
        # Manual Bounding Box inputs
        st.subheader("Enter Bounding Box Coordinates")
        st.markdown("Enter coordinates in decimal degrees:")
        
        bbox_col1, bbox_col2 = st.columns(2)
        with bbox_col1:
            min_lat = st.number_input("Min Latitude", value=40.4, format="%.4f", key="min_lat")
            min_lon = st.number_input("Min Longitude", value=-74.3, format="%.4f", key="min_lon")
        
        with bbox_col2:
            max_lat = st.number_input("Max Latitude", value=41.0, format="%.4f", key="max_lat")
            max_lon = st.number_input("Max Longitude", value=-73.5, format="%.4f", key="max_lon")
        
        # Generate bounding box
        bbox = f"POLYGON(({min_lon} {min_lat}, {min_lon} {max_lat}, {max_lon} {max_lat}, {max_lon} {min_lat}, {min_lon} {min_lat}))"
        
        # Show current bounding box
        st.markdown("### Current Bounding Box")
        st.code(bbox, language="text")

        if 'search_results' not in st.session_state:
            st.session_state.search_results = None
        if 'token' not in st.session_state:
            st.session_state.token = None

        if search_submitted:
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
                        
                        # Store download status in session state
                        download_key = f"download_{row['Id']}"
                        if download_key not in st.session_state:
                            st.session_state[download_key] = {
                                'started': False,
                                'completed': False,
                                'file_data': None,
                                'file_name': None
                            }

                        # Form for initiating download
                        with st.form(key=f"download_form_{row['Id']}"):
                            # Use the selected download directory from session state
                            downloads_dir = st.session_state.download_dir
                            
                            # Display the current download location
                            st.info(f"Files will be downloaded to: {downloads_dir}")
                            
                            download_submitted = st.form_submit_button("Start Download")

                        # Handle download process outside the form
                        download_state = st.session_state[download_key]
                        
                        if download_submitted:
                            download_state['started'] = True
                            progress_placeholder = st.empty()
                            status_placeholder = st.empty()
                            
                            # Define the async function outside
                            async def perform_download():
                                async with aiohttp.ClientSession() as session:
                                    result, file_exists = await download_product(
                                        session,
                                        row['Id'],
                                        st.session_state.token,
                                        row['Name'],
                                        downloads_dir,
                                        progress_placeholder,
                                        status_placeholder
                                    )
                                    
                                    # Store result in session state for later use
                                    if result and not file_exists:
                                        output_path, file_data = result
                                        file_name = os.path.basename(output_path)
                                        download_state['completed'] = True
                                        download_state['file_data'] = file_data
                                        download_state['file_name'] = file_name
                            
                            # Run the async function
                            asyncio.run(perform_download())
                        
                        # Display download button outside the form if download is complete
                        if download_state['completed'] and download_state['file_data'] is not None:
                            st.success("Download complete! You can now save the file to your browser.")
                            st.download_button(
                                label="Download to browser",
                                data=download_state['file_data'],
                                file_name=download_state['file_name'],
                                mime="application/zip",
                                key=f"browser_download_{row['Id']}"
                            )
                        elif download_state['completed'] and download_state['file_data'] is None:
                            st.success("Download complete! File was saved to your local disk.")
                            st.info(f"The file was too large to enable browser download. Please check the download directory: {downloads_dir}")
