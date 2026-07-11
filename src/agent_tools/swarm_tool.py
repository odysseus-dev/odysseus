"""
swarm_tool.py — Expose Swarm Intelligence to the agent loop.
Allows an agent to dynamically delegate a sub-task to a specialised swarm.
"""

import json
import logging

logger = logging.getLogger(__name__)

class DelegateToSwarmTool:
    """Tool that allows an agent to delegate a task to a Swarm."""
    
    async def execute(self, content: str, ctx: dict) -> dict:
        """
        Execute a swarm run synchronously from an agent tool call.
        content is expected to be a JSON string with swarm_id and query.
        """
        try:
            kwargs = json.loads(content)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON arguments for delegate_to_swarm.", "exit_code": 1}
            
        swarm_id = kwargs.get("swarm_id")
        query = kwargs.get("query")
        
        if not swarm_id or not query:
            return {"error": "swarm_id and query are required parameters.", "exit_code": 1}
            
        try:
            from core.database import SessionLocal, SwarmConfig
            from src.swarm.swarm_types import SwarmDefinition
            from src.swarm.swarm_manager import SwarmManager
            from src.swarm.swarm_definitions import get_builtin_swarm
        except ImportError as e:
            return {"error": f"Failed to import swarm modules: {e}", "exit_code": 1}

        # Resolve swarm
        swarm_def = get_builtin_swarm(swarm_id)
        if not swarm_def:
            db = SessionLocal()
            try:
                config = db.query(SwarmConfig).filter(
                    SwarmConfig.id == swarm_id,
                    SwarmConfig.owner == ctx.get("owner"),
                    SwarmConfig.is_active == True
                ).first()
                if config:
                    swarm_def = SwarmDefinition.from_dict(json.loads(config.definition))
            except Exception as e:
                logger.error(f"Failed to load swarm {swarm_id} from DB: {e}")
            finally:
                db.close()
                
        if not swarm_def:
            return {"error": f"Swarm '{swarm_id}' not found or inactive.", "exit_code": 1}

        owner = ctx.get("owner")
        session_id = ctx.get("session_id", "")
        endpoint_url = ctx.get("endpoint_url", "")
        model = ctx.get("model", "")
        
        manager = SwarmManager()
        
        try:
            final_result = ""
            async for chunk in manager.execute(
                swarm=swarm_def,
                user_query=query,
                session_id=session_id,
                owner=owner,
                endpoint_url=endpoint_url,
                model=model,
                messages=[{"role": "user", "content": query}]
            ):
                if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                    try:
                        data = json.loads(chunk[6:])
                        if "delta" in data:
                            final_result += data["delta"]
                    except Exception:
                        pass
                        
            if not final_result:
                return {"error": "Swarm executed but returned no output.", "exit_code": 1}
                
            return {"output": final_result, "exit_code": 0}
            
        except Exception as e:
            logger.exception("Swarm delegation failed")
            return {"error": f"Error during swarm execution: {str(e)}", "exit_code": 1}
