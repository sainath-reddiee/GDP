import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def test_macro_generation():
    print("Testing AI Macro Generation...")
    
    url = f"{BASE_URL}/mappings/macros/ai-generate"
    payload = {
        "prompt": "Create a macro to format a number as currency using USD."
    }
    
    try:
        start_time = time.time()
        print(f"Sending request to {url}...")
        response = requests.post(url, json=payload)
        duration = time.time() - start_time
        
        print(f"Response Status: {response.status_code}")
        print(f"Duration: {duration:.2f}s")
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                macro = data.get("payload", {})
                print("SUCCESS: Macro generated.")
                print(f"Name: {macro.get('name')}")
                print(f"Description: {macro.get('description')}")
                print("SQL Content:")
                print(macro.get("sql"))
            else:
                print(f"FAIL: API returned success=False. {data}")
        else:
            print(f"FAIL: HTTP {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    test_macro_generation()
