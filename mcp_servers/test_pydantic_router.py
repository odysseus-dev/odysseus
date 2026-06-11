import ollama
from pydantic import BaseModel
from pydantic_ai.mcp import MCPToolset
from fastmcp.client.transports import StdioTransport
import asyncio
import os
import sys
class AgentRouter(BaseModel):#This is the way that the AI formats its response so that values are easy to compare.
   Agent:str


    
async def test_Route(prompt, Agent_Hashmap:dict, system_prompt, model):
    response = await asyncio.to_thread(ollama.generate,                    #ollama.generate is synchronous so I created a new thread to carry out the execution.
    model=model,
    prompt= prompt,
    format=AgentRouter.model_json_schema(),
    system=system_prompt
     )
    Output = AgentRouter.model_validate_json(response['response']).Agent.lower()
    IdentifyingAgent = Agent_Hashmap.get(Output)#Maps it out with dictionary to look at what mcp agent should be called
    if not IdentifyingAgent:
        return
    BASE_DIR =  os.getcwd()
    filepath = os.path.join(BASE_DIR, "mcp_server", f"{IdentifyingAgent}")
    transport = StdioTransport(sys.executable, args=[filepath])
    calling_agent = MCPToolset(transport)
    return calling_agent
    