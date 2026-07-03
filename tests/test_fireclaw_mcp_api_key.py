import requests

def test_fireclaw_mcp_api_key():
    api_key = 'your_api_key_here'
    url = 'https://api.fireclawmcp.com/endpoint'  # Replace with the actual endpoint
    headers = {'Authorization': f'Bearer {api_key}'}
    
    response = requests.get(url, headers=headers)
    
    assert response.status_code == 200
    assert 'expected_key' in response.json()  # Replace 'expected_key' with a key you expect in the response

test_fireclaw_mcp_api_key()