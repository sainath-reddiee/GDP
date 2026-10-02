import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def test_transformation():
    print("Testing AI Transformation Generation...")
    
    url = f"{BASE_URL}/mappings/transformations/ai-generate"
    payload = {
        "prompt": "remove all whitespace from the column 'customer_name'"
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
                payload = data.get("payload", {})
                print("SUCCESS: Transformation generated.")
                print(f"SQL: {payload.get('sql')}")
            else:
                print(f"FAIL: API returned success=False. {data}")
        else:
            print(f"FAIL: HTTP {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    test_transformation()
