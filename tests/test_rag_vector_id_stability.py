import os
import subprocess
import sys

def test_rag_id_stability_across_processes():
    # Run helper in subprocesses with different PYTHONHASHSEED values to ensure cross-process stability
    cmd = [sys.executable, "-c", "from src.rag_vector import _generate_doc_id; print(_generate_doc_id('test_text_hash'))"]
    
    env0 = os.environ.copy()
    env0["PYTHONHASHSEED"] = "0"
    id0 = subprocess.check_output(cmd, env=env0).decode().strip()
    
    env1 = os.environ.copy()
    env1["PYTHONHASHSEED"] = "1"
    id1 = subprocess.check_output(cmd, env=env1).decode().strip()
    
    env_rand = os.environ.copy()
    env_rand["PYTHONHASHSEED"] = "random"
    id_rand = subprocess.check_output(cmd, env=env_rand).decode().strip()
    
    # Assert they are all equal (deterministic across seeds and processes)
    assert id0 == id1
    assert id0 == id_rand
    
    # Assert different inputs produce different IDs
    cmd_diff = [sys.executable, "-c", "from src.rag_vector import _generate_doc_id; print(_generate_doc_id('different_text_hash'))"]
    id_diff = subprocess.check_output(cmd_diff, env=env0).decode().strip()
    assert id0 != id_diff
