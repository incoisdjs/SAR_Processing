import os
import sys
import requests
import queue
import threading
import json

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

def main():
    if len(sys.argv) != 5:
        print("Usage: python download_handler.py <product_id> <token> <product_name> <output_dir>")
        sys.exit(1)
    
    product_id = sys.argv[1]
    token = sys.argv[2]
    product_name = sys.argv[3]
    output_dir = sys.argv[4]
    
    # Create progress queue
    progress_queue = queue.Queue()
    
    # Start download in a separate thread
    thread = threading.Thread(
        target=download_worker,
        args=(product_id, token, product_name, output_dir, progress_queue)
    )
    thread.start()
    
    # Monitor progress and print updates
    while True:
        try:
            status, *args = progress_queue.get(timeout=1)
            if status == 'progress':
                progress, downloaded, total_size = args
                print(json.dumps({
                    'status': 'progress',
                    'progress': progress,
                    'downloaded': downloaded,
                    'total_size': total_size
                }))
            elif status == 'complete':
                output_path = args[0]
                print(json.dumps({
                    'status': 'complete',
                    'path': output_path
                }))
                break
            elif status == 'error':
                error_msg = args[0]
                print(json.dumps({
                    'status': 'error',
                    'error': error_msg
                }))
                break
        except queue.Empty:
            continue

if __name__ == "__main__":
    main() 