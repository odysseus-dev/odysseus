from src.clean_agent_preview import verified_declared_workspace_artifacts
from PIL import Image


def test_verified_artifacts_count_as_completed_workspace_mutation(tmp_path):
    (tmp_path / "report.csv").write_text("name,value\na,1\n")
    Image.new('RGB', (1, 1), 'white').save(tmp_path / 'chart.png')
    prompt = "Create /workspace/report.csv and /workspace/chart.png"

    assert verified_declared_workspace_artifacts(prompt, str(tmp_path))


def test_missing_or_undeclared_artifacts_do_not_prove_a_mutation(tmp_path):
    (tmp_path / "report.csv").write_text("name,value\na,1\n")

    assert not verified_declared_workspace_artifacts(
        "Create /workspace/report.csv and /workspace/chart.png",
        str(tmp_path),
    )
    assert not verified_declared_workspace_artifacts(
        "Create the requested files",
        str(tmp_path),
    )


def test_placeholder_second_artifact_does_not_prove_completion(tmp_path):
    (tmp_path / 'report.csv').write_text('name,value\na,1\n')
    (tmp_path / 'chart.png').write_bytes(b'PNG')
    assert not verified_declared_workspace_artifacts(
        'Create /workspace/report.csv and /workspace/chart.png', str(tmp_path))
