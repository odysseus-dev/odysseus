import ollama
from pydantic import BaseModel
from pydantic_ai.mcp import MCPToolset
from fastmcp.client.transports import StdioTransport
import asyncio
class AgentRouter(BaseModel):#This is the way that the AI formats its response so that values are easy to compare.
   Agent:str



    

        
async def Route():
    ODYSSEUS_SERVERS = {            #Defined dictionary for key value pairs so AI response is mapped out to the correct file
    "rag":"rag_server.py",
    "image":"image_gen_server.py",
    "email":"email_server.py"
    }
    response = await asyncio.to_thread(ollama.generate,                    #ollama.generate is synchronous so I created a new thread to carry out the execution.
    model='llama3.2',
    prompt= "I want to send an email to my friend named billy.",
    format=AgentRouter.model_json_schema(),
    system= """Choose between three agents. 
     **Email agent responsible for sending an email.**
     **Image agent responsible for generating images.**
     ** rag agent for dealing with any external documents.**
     Make sure you say the name of the agent but don't add the word agent after it. For example, Email, Image, Rag respectively.


     """ 
     )
    Output = AgentRouter.model_validate_json(response['response']).Agent.lower()
    print(Output)
    IdentifyingAgent = ODYSSEUS_SERVERS.get(Output)#Maps it out with dictionary to look at what mcp agent should be called
    if not IdentifyingAgent:
        return 
    CallingAgent = MCPToolset(StdioTransport("python", args=[f"mcpservers/{IdentifyingAgent}"]))#Calls the agent.