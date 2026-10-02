import requests
import json
import sys

BASE_URL = "http://127.0.0.1:8000"
PROJECT_ID = 201

import time

def verify_generation():
    print(f"Verifying dbt generation for Project {PROJECT_ID}...")
    
    # Start generation
    start_url = f"{BASE_URL}/dbt-offline/csv/projects/{PROJECT_ID}/generate-dbt"
    try:
        response = requests.post(start_url)
        if response.status_code != 202:
            print(f"FAIL: Expected HTTP 202, got {response.status_code}")
            return

        print("Generation started. Polling status...")
        
        # Poll status
        status_url = f"{BASE_URL}/dbt-offline/csv/projects/{PROJECT_ID}/dbt/status"
        for _ in range(60):  # Wait up to 60 seconds
            status_resp = requests.get(status_url)
            status_data = status_resp.json().get("payload", {})
            status = status_data.get("status")
            
            print(f"  Status: {status}")
            if status == "COMPLETED":
                break
            elif status == "FAILED":
                print("FAIL: Generation failed.")
                return
            
            time.sleep(2)
        else:
            print("FAIL: Timed out waiting for generation.")
            return

        # Fetch generated files
        files_url = f"{BASE_URL}/dbt-offline/csv/projects/{PROJECT_ID}/dbt-files"
        files_resp = requests.get(files_url)
        files_data = files_resp.json()
        
        if files_data.get("success"):
            files_list = files_data.get("payload", [])
            print(f"SUCCESS: Generated {len(files_list)} files.")
            
            # Check for macro library
            macro_file = next((f for f in files_list if f["file_path"] == "macros/macro_library.sql"), None)
            if macro_file:
                print("  [PASS] macros/macro_library.sql exists.")
                print("  Content preview:")
                print("-" * 40)
                print(macro_file["file_content"][:300] + "...")
                print("-" * 40)
            else:
                print("  [FAIL] macros/macro_library.sql is MISSING.")
            
            # Check for silver models
            silver_count = sum(1 for f in files_list if f["file_path"].startswith("models/silver/"))
            print(f"  [PASS] Found {silver_count} Silver models.")
            
        else:
            print(f"FAIL: Could not fetch files. {files_data.get('message')}")

    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    verify_generation()
