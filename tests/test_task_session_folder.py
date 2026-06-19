"""Task sessions must be assigned folder='Tasks' at creation time."""
import inspect
from src.task_scheduler import TaskScheduler


def test_llm_task_session_gets_tasks_folder():
    """_execute_llm_task must create sessions with folder='Tasks'."""
    source = inspect.getsource(TaskScheduler._execute_llm_task)
    assert 'folder="Tasks"' in source or "folder='Tasks'" in source, (
        "LLM task session creation must set folder='Tasks'"
    )


def test_action_task_session_gets_tasks_folder():
    """_deliver_task_result must create sessions with folder='Tasks'."""
    source = inspect.getsource(TaskScheduler._deliver_task_result)
    assert 'folder="Tasks"' in source or "folder='Tasks'" in source, (
        "Action task session delivery must set folder='Tasks'"
    )


def test_research_task_session_gets_tasks_folder():
    """_execute_research_task must create sessions with folder='Tasks'."""
    source = inspect.getsource(TaskScheduler._execute_research_task)
    assert 'folder="Tasks"' in source or "folder='Tasks'" in source, (
        "Research task session creation must set folder='Tasks'"
    )
