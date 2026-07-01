def get_default_llm_prompt() -> str:
    return "You are a helpful AI assistant."

def get_legacy_preset_prompt() -> str:
    return "You are a helpful, balanced assistant. Match your response style to the user's needs."

def get_task_scheduler_prompt() -> str:
    return "You are a helpful assistant executing a scheduled task. Use available tools to complete the task thoroughly."