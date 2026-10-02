import requests
import json

BASE_URL = "http://127.0.0.1:8000"
PROJECT_ID = 201

def check_files():
    print(f"Checking generated files for Project {PROJECT_ID}...")
    
    # Check status first
    status_url = f"{BASE_URL}/dbt-offline/csv/projects/{PROJECT_ID}/dbt/status"
    try:
        status_resp = requests.get(status_url)
        print(f"Status Code: {status_resp.status_code}")
        print(f"Status Text: {status_resp.text}")
        print(f"Current Status: {status_resp.json()}")
        
        # Fetch files
        files_url = f"{BASE_URL}/dbt-offline/csv/projects/{PROJECT_ID}/dbt-files"
        files_resp = requests.get(files_url)
        print(f"Files Resp Code: {files_resp.status_code}")
        # print(f"Files Resp Content: {files_resp.text}") # Uncomment if needed
        files_data = files_resp.json()
        
        if files_data.get("success"):
            files_list = files_data.get("payload", [])
            print(f"Found {len(files_list)} files.")
            
            macro_file = next((f for f in files_list if f["file_path"] in ["macros/macro_library.sql", "macros\\macro_library.sql"]), None)
            if macro_file:
                print("SUCCESS: macros/macro_library.sql FOUND.")
                print("--- CONTENT PREVIEW ---")
                print(macro_file["file_content"][:500])
                print("-----------------------")
            else:
                print("FAIL: macros/macro_library.sql NOT FOUND.")
                print("Files found: ", [f["file_path"] for f in files_list])

            silver_files = [f for f in files_list if "models/silver" in f["file_path"].replace("\\", "/")]
            print(f"Found {len(silver_files)} Silver models: {[f['file_path'] for f in silver_files]}")

        else:
            print(f"Error fetching files: {files_data}")
            
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    check_files()
