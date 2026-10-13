"""
Simple test script to verify the email performance endpoint works.
"""

import requests

def test_email_performance_endpoint():
    try:
        response = requests.get("http://localhost:8000/email/performance-report")
        if response.status_code == 200:
            data = response.json()
            print("✅ Email performance endpoint works!")
            print(f"SMTP Pool Size: {data.get('smtp_pool_size', 0)}")
            print(f"IMAP Pool Size: {data.get('imap_pool_size', 0)}")
            print(f"Connection Metrics: {data.get('connection_metrics', {})}")
            print(f"Recent Errors: {data.get('recent_errors', [])}")
        else:
            print(f"❌ Endpoint returned status code: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Error testing endpoint: {e}")

if __name__ == "__main__":
    test_email_performance_endpoint()