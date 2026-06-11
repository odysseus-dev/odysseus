import json
import logging

logger = logging.getLogger(__name__)

MAX_PIPELINE_STEPS = 10


class PipelineTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Execute a multi-step pipeline where each model's output feeds the next.

        Content format (JSON):
        {"steps": [
            {"model": "model_a", "instruction": "Draft an essay about X"},
            {"model": "model_b", "instruction": "Critique the following draft"},
            {"model": "model_a", "instruction": "Revise based on this critique"}
        ]}

        Or line format:
        Line 1: step1_model | step1_instruction
        Line 2: step2_model | step2_instruction
        ..."""
        owner = ctx.get("owner")
        from src.ai_interaction import _resolve_model, AI_CHAT_TIMEOUT
        from src.llm_core import llm_call_async

        # Try JSON parse first
        steps = None
        try:
            data = json.loads(content.strip())
            if isinstance(data, dict) and "steps" in data:
                steps = data["steps"]
            elif isinstance(data, list):
                steps = data
        except (json.JSONDecodeError, TypeError):
            pass

        # Fall back to line format: model | instruction
        if not steps:
            steps = []
            for line in content.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                if "|" in line:
                    parts = line.split("|", 1)
                    steps.append({"model": parts[0].strip(), "instruction": parts[1].strip()})
                else:
                    return {"error": "Each line must be: model | instruction (or use JSON format)"}

        if not steps:
            return {"error": "No pipeline steps provided"}
        if len(steps) > MAX_PIPELINE_STEPS:
            return {"error": f"Maximum {MAX_PIPELINE_STEPS} steps allowed"}

        # Resolve all models first (fail fast)
        resolved = []
        for i, step in enumerate(steps):
            model_spec = step.get("model", "").strip()
            instruction = step.get("instruction", "").strip()
            if not model_spec or not instruction:
                return {"error": f"Step {i + 1}: both 'model' and 'instruction' are required"}
            try:
                url, model, headers = _resolve_model(model_spec, owner=owner)
                resolved.append((url, model, headers, instruction))
            except ValueError as e:
                return {"error": f"Step {i + 1}: {e}"}

        # Execute pipeline
        step_outputs = []
        previous_output = None

        try:
            for i, (url, model, headers, instruction) in enumerate(resolved):
                if previous_output:
                    user_content = (
                        f"Previous step's output:\n\n{previous_output}\n\n"
                        f"Your task: {instruction}"
                    )
                else:
                    user_content = instruction

                messages = [
                    {"role": "system", "content": f"You are step {i + 1} in a processing pipeline. {instruction}"},
                    {"role": "user", "content": user_content},
                ]

                response = await llm_call_async(
                    url, model, messages, headers=headers, timeout=AI_CHAT_TIMEOUT
                )

                step_outputs.append({
                    "step": i + 1,
                    "model": model,
                    "instruction": instruction,
                    "output": response[:5000] if len(response) > 5000 else response,
                })

                previous_output = response

            result_lines = [f"# Pipeline Results ({len(resolved)} steps)\n"]
            for so in step_outputs:
                result_lines.append(f"## Step {so['step']}: {so['model']}")
                result_lines.append(f"*Instruction: {so['instruction']}*\n")
                result_lines.append(so["output"])
                result_lines.append("\n---\n")

            return {
                "results": "\n".join(result_lines),
                "steps": step_outputs,
                "final_output": previous_output,
            }
        except Exception as e:
            logger.error(f"pipeline failed at step {len(step_outputs) + 1}: {e}")
            return {"error": f"Pipeline failed at step {len(step_outputs) + 1}: {e}"}
