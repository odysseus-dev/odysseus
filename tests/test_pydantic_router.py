import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# 1. Import your actual production function
from src.builtin_mcp import RouterCaller

def test_router_caller_execution():
    # 2. Setup your mock data structures exactly like your production schema
    test_agent_hashmap = {
        'Agent': {
            'EmailAgent': 'mcp_servers/email_agent.py'
        },
        'Tool': {
            'EmailAgent': {
                'send_email': 'execute_smtp_send'
            }
        }
    }
    
    test_args = {
        'send_email': {
            'recipient': 'test@example.com',
            'body': 'Hello World'
        }
    }

    # 3. Create a fake router module so it doesn't crash looking for live packages
    mock_router_module = MagicMock()
    mock_router_module.routeMCP = AsyncMock(return_value="Success")

    # 4. Patch the importlib disk read
    with patch("importlib.util.spec_from_file_location"), \
         patch("importlib.util.module_from_spec", return_value=mock_router_module):

        # 5. CALL YOUR ROUTERCALLER AND JUST PASS IT IN!
        result = asyncio.run(RouterCaller(
            prompt="Send a test email",
            Agent_Hashmap=test_agent_hashmap,
            AgentSystemPrompt="You are a routing agent",
            model="llama3.2",
            args=test_args,
            ToolSystemPrompt="Validate inputs"
        ))

        # 6. Assert that your code correctly caught and returned the output
        assert result == "Success"

    