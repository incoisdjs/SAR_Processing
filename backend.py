from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
import json
import subprocess
from typing import List, Optional
import uvicorn
import asyncio
from fastapi.responses import StreamingResponse
import io

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add this at the top after imports
download_statuses = {}

class SearchRequest(BaseModel):
    username: str
    password: str
    bbox: str
    collection: str
    start_date: str
    end_date: str
    result_limit: int = 1000

class DownloadRequest(BaseModel):
    product_id: str
    token: str
    product_name: str
    output_dir: str

class DownloadStatus(BaseModel):
    status: str
    progress: float
    downloaded: int
    total_size: int
    error: Optional[str] = None

def get_keycloak_token(username: str, password: str) -> str:
    """Get access token from Copernicus using username and password"""
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
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")

def search_products(token: str, bbox: str, collection: str, start_date: str, end_date: str, result_limit: int = 1000):
    """Search for products in the Copernicus catalog"""
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
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

def run_download_process(product_id: str, token: str, product_name: str, output_dir: str):
    """Run the download process in a separate process"""
    try:
        process = subprocess.Popen(
            [
                'python', 'download_handler.py',
                product_id,
                token,
                product_name,
                output_dir
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return process
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download process failed to start: {str(e)}")

@app.post("/search")
async def search(request: SearchRequest):
    """Search for products in the Copernicus catalog"""
    try:
        token = get_keycloak_token(request.username, request.password)
        results = search_products(
            token=token,
            bbox=request.bbox,
            collection=request.collection,
            start_date=request.start_date,
            end_date=request.end_date,
            result_limit=request.result_limit
        )
        return {"token": token, "results": results}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/download")
async def download(request: DownloadRequest):
    """Start a download process"""
    try:
        if not os.path.exists(request.output_dir):
            os.makedirs(request.output_dir)
        
        # Initialize download status
        download_id = f"{request.product_id}_{request.product_name}"
        download_statuses[download_id] = {
            'status': 'started',
            'progress': 0,
            'downloaded': 0,
            'total_size': 0,
            'error': None
        }
        
        # Start download in a background task
        asyncio.create_task(download_worker(
            request.product_id,
            request.token,
            request.product_name,
            request.output_dir,
            download_id
        ))
        
        return {"status": "started", "download_id": download_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/status/{download_id}")
async def get_download_status(download_id: str):
    """Get the status of a download"""
    if download_id not in download_statuses:
        raise HTTPException(status_code=404, detail="Download not found")
    return download_statuses[download_id]

async def download_worker(product_id: str, token: str, product_name: str, output_dir: str, download_id: str):
    """Worker function to handle download in a separate task"""
    try:
        output_path = os.path.join(output_dir, f"{product_name}.zip")
        session = requests.Session()
        session.headers.update({"Authorization": f"Bearer {token}"})
        
        url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"
        
        print(f"\n[DEBUG] Starting download for product: {product_name}")
        print(f"[DEBUG] Output path: {output_path}")
        
        # Follow redirects
        print("[DEBUG] Following redirects...")
        response = session.get(url, allow_redirects=False)
        while response.status_code in (301, 302, 303, 307):
            url = response.headers["Location"]
            print(f"[DEBUG] Redirected to: {url}")
            response = session.get(url, allow_redirects=False)
        
        # Get file size
        print("[DEBUG] Getting file size...")
        file_response = session.get(url, stream=True, verify=False)
        total_size = int(file_response.headers.get('content-length', 0))
        print(f"[DEBUG] Total file size: {total_size / (1024*1024):.2f} MB")
        
        # Update status with total size
        download_statuses[download_id]['total_size'] = total_size
        
        # Download with progress
        print("[DEBUG] Starting download...")
        downloaded = 0
        last_progress = 0
        with open(output_path, "wb") as f:
            for chunk in file_response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    progress = (downloaded / total_size) * 100 if total_size > 0 else 0
                    
                    # Only print progress every 5% to avoid flooding the console
                    if int(progress) > last_progress and int(progress) % 5 == 0:
                        print(f"[DEBUG] Download progress: {progress:.1f}% ({downloaded / (1024*1024):.2f} MB / {total_size / (1024*1024):.2f} MB)")
                        last_progress = int(progress)
                    
                    download_statuses[download_id].update({
                        'status': 'progress',
                        'progress': progress,
                        'downloaded': downloaded
                    })
        
        print(f"[DEBUG] Download complete! File saved to: {output_path}")
        download_statuses[download_id].update({
            'status': 'complete',
            'progress': 100,
            'downloaded': total_size,
            'path': output_path
        })
    except Exception as e:
        print(f"[DEBUG] Download error: {str(e)}")
        download_statuses[download_id].update({
            'status': 'error',
            'error': str(e)
        })

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000) 