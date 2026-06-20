import ollama
from pydantic import BaseModel
from pydantic_ai.mcp import MCPToolset
from fastmcp.client.transports import StdioTransport
from typing import Optional
import asyncio
import os
import sys
from pathlib import Path

# Path(__file__).parent evaluates to exactly the /app/mcp_servers/ directory

from typing import Annotated

class AgentRouter(BaseModel):
    Agent:str



class ToolRouter(BaseModel):#This is the way that the AI formats its response so that values are easy to compare.
   Tool_To_Call:Annotated[str, "Enter the name of the tool that needs to be used."]

    
async def routeMCP(prompt, Agent_Hashmap:dict,AgentSystemPrompt, model, args:dict, ToolSystemPrompt):
    """For the Agent Hashmap it should be written as follows:
    'Agent':{
       {AgentName:FileOfTheAgent.py}},
    'Tool':{
    'AgentName':{
       {ToolName:FunctionOfTheTool}}}
    The Args should be written like this
    {toolName:args.} Ensure args is provided as a dictionary where the function of your tool params is provided as a key and the payload as the value."""
    with open("Oldfile.txt", "a") as f:
        f.write("Actually might be working")
    response = await asyncio.to_thread(ollama.generate,                    #ollama.generate is synchronous so I created a new thread to carry out the execution.
    model=model,
    prompt= prompt,
    format=AgentRouter.model_json_schema(),
    system=AgentSystemPrompt+f"""Names of Agent/s:{str(Agent_Hashmap['Agent'].keys()).split("dict_keys")[1]}.""",
     )
    AgentType = AgentRouter.model_validate_json(response['response']).Agent
    Tool_Identification = await asyncio.to_thread(ollama.generate, model = model, prompt = f"Here is the prompt {prompt}:Here are the ToolNames:[{str(Agent_Hashmap["Tool"][AgentType].keys()).split("dict_keys")[1]}.].", format = ToolRouter.model_json_schema(), system = ToolSystemPrompt)
    ToolType = ToolRouter.model_validate_json(Tool_Identification["response"]).Tool_To_Call


    IdentifyingAgent = Agent_Hashmap["Agent"].get(AgentType)#Maps it out with dictionary to look at what mcp agent should be called
    IdentifyingTool = Agent_Hashmap["Tool"][AgentType].get(ToolType)
    if not IdentifyingAgent:
        return
    filepath = (Path(__file__).parent / IdentifyingAgent).resolve().as_posis()

    transport = StdioTransport(sys.executable, args=["-u", filepath])
    async with MCPToolset(transport) as calling_agent:
        mcp_client = calling_agent.client
        tools = await mcp_client.list_tools()
        for tool in tools:
            
                result = await calling_agent.client.call_tool(
                    name=IdentifyingTool, 
                    arguments=args[ToolType]
                )
                if result and hasattr(result, 'content') and len(result.content) > 0:
                    return result.content
                return result
   




    