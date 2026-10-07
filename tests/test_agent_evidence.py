import json

from src.agent_evidence import (
    CompletionRequirements,
    CompletionStatus,
    EvidenceKind,
    EvidenceLedger,
    infer_completion_requirements,
    requirements_from_runtime_context,
)


def test_infers_only_explicit_output_or_edit_paths():
    requirements = infer_completion_requirements(
        "Inspect evidence.png, then write answer.json and verify it."
    )

    assert requirements.required_artifacts == ("answer.json",)
    assert requirements.verifier_required is True


def test_known_workspace_inputs_are_not_inferred_as_output_artifacts():
    requirements = infer_completion_requirements(
        "My clues are saved in /tmp_workspace/inputs/clues.md, the required answer "
        "template is /tmp_workspace/inputs/template.md, and a post-write verification "
        "list is /tmp_workspace/inputs/validation_checklist.txt. Read all three first. "
        "Fill in /tmp_workspace/inputs/template.md exactly and save the completed "
        "document to /tmp_workspace/results/results.md.",
        known_input_paths=(
            "/workspace/inputs/clues.md",
            "/workspace/inputs/template.md",
            "/workspace/inputs/validation_checklist.txt",
        ),
    )

    assert requirements.required_artifacts == (
        "/tmp_workspace/results/results.md",
    )


def test_known_workspace_input_remains_required_when_explicitly_updated():
    requirements = infer_completion_requirements(
        "Update /tmp_workspace/inputs/config.json and then verify it.",
        known_input_paths=("/workspace/inputs/config.json",),
    )

    assert requirements.required_artifacts == (
        "/tmp_workspace/inputs/config.json",
    )


def test_infers_artifact_from_common_past_participle_request():
    requirements = infer_completion_requirements(
        "Build a digest saved to /workspace/results/ops_digest.md."
    )

    assert requirements.required_artifacts == (
        "/workspace/results/ops_digest.md",
    )


def test_inference_ignores_negated_edits_and_callable_names():
    requirements = infer_completion_requirements(
        "Write /workspace/results/predictions.json serialized with "
        "`json.dumps(..., indent=2)`. Do not modify `segmenter.py` or `cases.jsonl`."
    )

    assert requirements.required_artifacts == (
        "/workspace/results/predictions.json",
    )


def test_inference_ignores_prose_abbreviations_that_look_like_paths():
    requirements = infer_completion_requirements(
        "Generate statistics, e.g. token counts and timing totals."
    )

    assert requirements.required_artifacts == ()


def test_inference_ignores_bare_existence_helper_as_artifact():
    requirements = infer_completion_requirements(
        "Write /workspace/results/answer.json, then verify it with os.path.exists or ls."
    )

    assert requirements.required_artifacts == ("/workspace/results/answer.json",)


def test_inference_recognizes_named_output_file():
    requirements = infer_completion_requirements(
        "Put the implementation in a file called /workspace/worker.py."
    )

    assert requirements.required_artifacts == ("/workspace/worker.py",)


def test_inference_recognizes_output_into_a_single_file():
    requirements = infer_completion_requirements(
        "Reconcile reliable facts into a single file "
        "/tmp_workspace/results/profile.md. Save only that file in "
        "/tmp_workspace/results/."
    )

    assert requirements.required_artifacts == ("/tmp_workspace/results/profile.md",)


def test_output_directory_outranks_relative_example_filenames():
    requirements = infer_completion_requirements(
        "Save the results into `/tmp_workspace/results`. Save each recovered table "
        "as a separate file named `1.tex`, `2.tex`, `3.tex`, ..."
    )

    assert requirements.required_artifacts == ("/tmp_workspace/results",)


def test_inference_recognizes_localized_output_directory():
    requirements = infer_completion_requirements(
        "创建 `/tmp_workspace/results/` 目录，并在该目录下创建五个分类子目录。"
    )

    assert requirements.required_artifacts == ("/tmp_workspace/results",)


def test_inference_continues_past_example_to_explicit_output_file():
    requirements = infer_completion_requirements(
        'Write an integer (e.g. "1000000") to the file /app/answer.txt.'
    )

    assert requirements.required_artifacts == ("/app/answer.txt",)


def test_inference_recognizes_localized_output_request():
    requirements = infer_completion_requirements(
        "结果保存为.md文件，保存到 `/tmp_workspace/results/results.md`"
    )

    assert requirements.required_artifacts == ("/tmp_workspace/results/results.md",)


def test_runtime_requirements_override_instruction_inference():
    requirements = requirements_from_runtime_context(
        {
            "completion_requirements": {
                "required_artifacts": ["/workspace/output.html"],
                "verifier_required": False,
                "executable_verifier_available": True,
                "verifier_commands": ["./test.sh"],
            }
        },
        instruction="write ignored.json",
    )

    assert requirements.required_artifacts == ("/workspace/output.html",)
    assert requirements.executable_verifier_available is True
    assert requirements.verifier_commands == ("./test.sh",)


def test_runtime_requirements_drop_media_inputs_misclassified_as_outputs():
    requirements = requirements_from_runtime_context(
        {
            "media_ingress": {
                "artifacts": [
                    {"source_path": "/workspace/inputs/clues.md"},
                    {"source_path": "/workspace/inputs/template.md"},
                ],
            },
            "completion_requirements": {
                "required_artifacts": [
                    "/tmp_workspace/inputs/clues.md",
                    "/tmp_workspace/inputs/template.md",
                    "/tmp_workspace/results/results.md",
                ],
                "verifier_required": True,
            },
        },
        instruction=(
            "Clues are saved in /tmp_workspace/inputs/clues.md and the template is "
            "/tmp_workspace/inputs/template.md. Save the completed document to "
            "/tmp_workspace/results/results.md."
        ),
    )

    assert requirements.required_artifacts == (
        "/tmp_workspace/results/results.md",
    )


def test_available_verifier_is_required_and_declared_command_is_evidence():
    requirements = infer_completion_requirements(
        "Update app.py",
        executable_verifier_available=True,
        verifier_commands=("./test.sh",),
    )
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "edit_file",
                "command": '{"path":"app.py","old_string":"a","new_string":"b"}',
                "output": "edited app.py",
                "exit_code": 0,
            },
            {
                "round": 2,
                "tool": "bash",
                "command": "./test.sh",
                "output": "ok",
                "exit_code": 0,
            },
        ],
        requirements,
    )

    assert requirements.verifier_required is True
    assert ledger.evaluate().status == CompletionStatus.VERIFIED


def test_successful_write_satisfies_declared_artifact_without_claiming_verification():
    requirements = infer_completion_requirements("Write answer.json")
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "write_file",
                "command": "answer.json\n{\"ok\": true}",
                "output": "wrote answer.json",
                "exit_code": 0,
            }
        ],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.SATISFIED
    assert decision.can_complete is True
    assert len(decision.evidence_ids) == 1


def test_private_browser_screenshot_satisfies_declared_artifact():
    requirements = infer_completion_requirements(
        "Render the generated HTML as an image and save as /workspace/output.png."
    )
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 2,
                "tool": "private_browser",
                "command": '{"action":"screenshot","path":"/workspace/output.png"}',
                "output": "Screenshot saved",
                "exit_code": 0,
            }
        ],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.SATISFIED
    assert decision.can_complete is True


def test_failed_retry_does_not_erase_successful_artifact_mutation():
    requirements = infer_completion_requirements("Write answer.json")
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "write_file",
                "command": "answer.json\n{\"ok\": true}",
                "output": "wrote answer.json",
                "exit_code": 0,
            },
            {
                "round": 2,
                "tool": "write_file",
                "command": "answer.json\ninvalid retry",
                "error": "write failed",
                "exit_code": 1,
            },
        ],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.SATISFIED
    assert decision.can_complete is True
    assert len(decision.evidence_ids) == 1


def test_missing_declared_artifact_blocks_completion():
    requirements = infer_completion_requirements("Write answer.json")
    ledger = EvidenceLedger.from_tool_events([], requirements)

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.BLOCKED
    assert decision.missing_artifacts == ("answer.json",)


def test_trailing_slash_output_directory_outranks_template_tex_filename():
    instruction = """
    You must create the following outputs under `/tmp_workspace/results/`:
    - `2022.tsv`
    - one or more paper source `.tex` files named `{title}_v1.tex`
    """

    requirements = infer_completion_requirements(instruction)

    assert requirements.required_artifacts == ("/tmp_workspace/results",)


def test_python_directory_copy_and_symlink_operations_are_artifact_mutations():
    requirements = infer_completion_requirements(
        "Create outputs under /tmp_workspace/results/"
    )
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "python",
            "command": (
                "from pathlib import Path\n"
                "import shutil\n"
                "target = Path('/tmp_workspace/results')\n"
                "target.mkdir(parents=True, exist_ok=True)\n"
                "shutil.copy2('/tmp_workspace/0.png', target / '0.png')\n"
            ),
            "output": "copied",
            "exit_code": 0,
        }],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.can_complete is True
    assert decision.status == CompletionStatus.SATISFIED


def test_python_symlink_to_is_artifact_mutation():
    requirements = infer_completion_requirements(
        "Create outputs under /tmp_workspace/results/"
    )
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "python",
            "command": (
                "from pathlib import Path\n"
                "target = Path('/tmp_workspace/results')\n"
                "target.mkdir(parents=True, exist_ok=True)\n"
                "(target / '0.png').symlink_to('/tmp_workspace/0.png')\n"
            ),
            "output": "linked",
            "exit_code": 0,
        }],
        requirements,
    )

    assert ledger.evaluate().can_complete is True


def test_reading_preexisting_artifact_does_not_satisfy_mutation_request():
    requirements = infer_completion_requirements("Update app.py")
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "bash",
            "command": "cat app.py",
            "output": "VALUE = 1",
            "exit_code": 0,
        }],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.BLOCKED
    assert decision.missing_artifacts == ("app.py",)


def test_latest_authoritative_verifier_result_wins():
    requirements = infer_completion_requirements("Update app.py, then run pytest")
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "edit_file",
                "command": '{"path":"app.py","old_string":"a","new_string":"b"}',
                "output": "edited app.py",
                "exit_code": 0,
            },
            {
                "round": 2,
                "tool": "bash",
                "command": "pytest -q",
                "output": "1 failed",
                "exit_code": 1,
            },
        ],
        requirements,
    )

    assert ledger.evaluate().status == CompletionStatus.FAILED
    ledger.record_tool_event(
        {
            "round": 3,
            "tool": "bash",
            "command": "pytest -q",
            "output": "1 passed",
            "exit_code": 0,
        }
    )
    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.VERIFIED
    assert decision.can_complete is True


def test_passing_verifier_before_latest_mutation_is_stale():
    requirements = infer_completion_requirements(
        "Update app.py",
        executable_verifier_available=True,
        verifier_commands=("./test.sh",),
    )
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "bash",
                "command": "./test.sh",
                "output": "ok",
                "exit_code": 0,
            },
            {
                "round": 2,
                "tool": "edit_file",
                "command": '{"path":"app.py","old_string":"a","new_string":"b"}',
                "output": "edited app.py",
                "exit_code": 0,
            },
        ],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.BLOCKED
    assert "predates" in decision.reason


def test_successful_non_verification_shell_command_is_not_verifier_evidence():
    requirements = infer_completion_requirements("Update app.py, then verify it")
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "edit_file",
                "command": '{"path":"app.py","old_string":"a","new_string":"b"}',
                "output": "edited app.py",
                "exit_code": 0,
            },
            {
                "round": 2,
                "tool": "bash",
                "command": "pwd",
                "output": "/workspace",
                "exit_code": 0,
            },
        ],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.BLOCKED
    assert "no current artifact validation" in decision.reason


def test_artifact_inspection_satisfies_natural_language_verification_request():
    requirements = infer_completion_requirements("Write answer.json, then verify it")
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "write_file",
                "command": "answer.json\n{}",
                "output": "wrote answer.json",
                "exit_code": 0,
            },
            {
                "round": 2,
                "tool": "bash",
                "command": "cat answer.json",
                "output": "{}",
                "exit_code": 0,
            },
        ],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.SATISFIED
    assert decision.can_complete is True


def test_shell_redirect_write_is_not_artifact_validation():
    requirements = infer_completion_requirements("Write answer.json")
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "bash",
            "command": "cat > answer.json <<'EOF'\n{}\nEOF",
            "output": "",
            "exit_code": 0,
        }],
        requirements,
    )

    assert ledger.evaluate().can_complete is True
    assert not any(
        event.kind == EvidenceKind.ARTIFACT_VALIDATION
        for event in ledger.events
    )


def test_compiler_check_must_be_repeated_after_artifact_rewrite():
    requirements = infer_completion_requirements("Update proof.v")
    events = [
        {
            "round": 1,
            "tool": "write_file",
            "command": "proof.v\nfirst version",
            "output": "wrote proof.v",
            "exit_code": 0,
        },
        {
            "round": 2,
            "tool": "bash",
            "command": "coqc proof.v",
            "output": "compile error",
            "exit_code": 1,
        },
        {
            "round": 3,
            "tool": "write_file",
            "command": "proof.v\nsecond version",
            "output": "wrote proof.v",
            "exit_code": 0,
        },
    ]
    ledger = EvidenceLedger.from_tool_events(events, requirements)

    decision = ledger.evaluate()
    assert decision.can_complete is False
    assert "validation predates" in decision.reason

    ledger.record_tool_event({
        "round": 4,
        "tool": "bash",
        "command": "coqc proof.v",
        "output": "",
        "exit_code": 0,
    })
    decision = ledger.evaluate()
    assert decision.can_complete is True
    assert "validation evidence" in decision.reason


def test_python_artifact_write_is_detected_against_declared_path():
    requirements = infer_completion_requirements("Write answer.json")
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "python",
                "command": 'open("/workspace/answer.json", "w").write("{}")',
                "output": "",
                "exit_code": 0,
            }
        ],
        requirements,
    )

    assert ledger.evaluate().status == CompletionStatus.SATISFIED
    assert any(event.kind == EvidenceKind.ARTIFACT_MUTATION for event in ledger.events)


def test_json_write_file_then_read_records_required_artifact_mutation():
    """Native compact tool events carry write_file arguments as JSON."""

    requirements = CompletionRequirements(
        required_artifacts=("/workspace/results/paper_digest.md",)
    )
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "write_file",
                "command": json.dumps({
                    "path": "/workspace/results/paper_digest.md",
                    "content": "# Verified digest\n",
                }),
                "output": "wrote /workspace/results/paper_digest.md",
                "exit_code": 0,
            },
            {
                "round": 2,
                "tool": "read_file",
                "command": json.dumps({
                    "path": "/workspace/results/paper_digest.md",
                }),
                "output": "# Verified digest\n",
                "exit_code": 0,
            },
        ],
        requirements,
    )

    assert ledger.evaluate().status == CompletionStatus.SATISFIED
    assert any(
        event.kind == EvidenceKind.ARTIFACT_MUTATION
        and event.artifact_path == "/workspace/results/paper_digest.md"
        for event in ledger.events
    )


def test_read_file_after_write_satisfies_required_artifact_validation():
    requirements = CompletionRequirements(
        required_artifacts=("/workspace/results/paper_digest.md",),
        verifier_required=True,
    )
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "write_file",
                "command": json.dumps({
                    "path": "/workspace/results/paper_digest.md",
                    "content": "# Verified digest\n",
                }),
                "output": "wrote /workspace/results/paper_digest.md",
                "exit_code": 0,
            },
            {
                "round": 2,
                "tool": "read_file",
                "command": json.dumps({
                    "path": "/workspace/results/paper_digest.md",
                }),
                "output": "# Verified digest\n",
                "exit_code": 0,
            },
        ],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.SATISFIED
    assert decision.can_complete is True


def test_inspect_media_export_satisfies_declared_artifact():
    requirements = infer_completion_requirements("Save /workspace/frame.png")
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "inspect_media",
            "command": '{"path":"/workspace/video.mp4","timestamp":"5",'
                       '"output_path":"/workspace/frame.png"}',
            "output": "Created still image",
            "exit_code": 0,
        }],
        requirements,
    )

    assert ledger.evaluate().status == CompletionStatus.SATISFIED


def test_inspect_media_batch_exports_satisfy_declared_artifacts():
    requirements = CompletionRequirements(required_artifacts=(
        "/workspace/one.png", "/workspace/two.png"
    ))
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "inspect_media",
            "command": '{"path":"/workspace/video.mp4","exports":['
                       '{"timestamp":"1","output_path":"/workspace/one.png"},'
                       '{"timestamp":"2","output_path":"/workspace/two.png"}]}',
            "output": "Created still images",
            "exit_code": 0,
        }],
        requirements,
    )

    assert ledger.evaluate().status == CompletionStatus.SATISFIED


def test_python_image_save_satisfies_declared_artifact():
    requirements = infer_completion_requirements("Save /workspace/poster.png")
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "python",
            "command": 'poster.save("/workspace/poster.png")',
            "output": "saved",
            "exit_code": 0,
        }],
        requirements,
    )

    assert ledger.evaluate().status == CompletionStatus.SATISFIED


def test_python_dataframe_export_satisfies_declared_artifact():
    requirements = infer_completion_requirements("Export /workspace/results.csv")
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "python",
            "command": 'frame.to_csv("/workspace/results.csv", index=False)',
            "output": "",
            "exit_code": 0,
        }],
        requirements,
    )

    assert ledger.evaluate().status == CompletionStatus.SATISFIED


def test_latest_failed_mutation_invalidates_earlier_success_for_required_artifact():
    requirements = CompletionRequirements(
        required_artifacts=("/workspace/results.jsonl",)
    )
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "write_file",
                "command": "/workspace/results.jsonl\n{\"value\": 1}\n",
                "output": "wrote results",
                "exit_code": 0,
            },
            {
                "round": 2,
                "tool": "python",
                "command": (
                    "with open('/workspace/results.jsonl', 'w') as f:\n"
                    "    raise RuntimeError('conversion failed')"
                ),
                "output": "RuntimeError: conversion failed",
                "exit_code": 1,
            },
        ],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.BLOCKED
    assert decision.missing_artifacts == ("/workspace/results.jsonl",)


def test_write_file_helper_script_does_not_satisfy_artifact_it_mentions():
    requirements = CompletionRequirements(
        required_artifacts=(
            "/workspace/meta_analysis.csv",
            "/workspace/cross_benchmark_comparison.png",
        )
    )
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "write_file",
            "command": (
                "/workspace/download_and_analyze.py\n"
                "import pandas as pd\n"
                "pd.DataFrame().to_csv('/workspace/meta_analysis.csv')\n"
            ),
            "output": "wrote helper script",
            "exit_code": 0,
        }],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.BLOCKED
    assert decision.missing_artifacts == (
        "/workspace/meta_analysis.csv",
        "/workspace/cross_benchmark_comparison.png",
    )


def test_absolute_required_artifact_requires_exact_path_not_same_basename():
    requirements = CompletionRequirements(
        required_artifacts=("/workspace/meta_analysis.csv",)
    )
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "write_file",
            "command": (
                "/workspace/papers/meta_analysis.csv\n"
                "Model,RefCOCO-avg,ERQA\n"
            ),
            "output": "wrote nested csv",
            "exit_code": 0,
        }],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.BLOCKED
    assert decision.missing_artifacts == ("/workspace/meta_analysis.csv",)


def test_sed_in_place_edit_is_recorded_as_artifact_mutation():
    requirements = infer_completion_requirements("Update /workspace/app.py")
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "bash",
            "command": "sed -i 's/VALUE = 1/VALUE = 2/' /workspace/app.py",
            "output": "",
            "exit_code": 0,
        }],
        requirements,
    )

    assert any(
        event.kind == EvidenceKind.ARTIFACT_MUTATION
        and event.artifact_path == "/workspace/app.py"
        for event in ledger.events
    )


def test_evidence_records_hashes_not_raw_command_or_output():
    secret = "super-secret-value"
    ledger = EvidenceLedger.from_tool_events(
        [
            {
                "round": 1,
                "tool": "bash",
                "command": f"echo {secret}",
                "output": secret,
                "exit_code": 0,
            }
        ]
    )

    serialized = str(ledger.to_list())
    assert secret not in serialized
    assert len(ledger.events[0].command_sha256) == 64
    assert len(ledger.events[0].output_sha256) == 64


def test_successful_tool_event_does_not_complete_when_workspace_artifact_is_missing(tmp_path):
    requirements = CompletionRequirements(
        required_artifacts=("/workspace/output.txt",),
        workspace_root=str(tmp_path),
    )
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "write_file",
            "command": "/workspace/output.txt",
            "output": "wrote 12 bytes",
            "exit_code": 0,
        }],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.BLOCKED
    assert decision.missing_artifacts == ("/workspace/output.txt",)


def test_workspace_artifact_must_not_be_empty(tmp_path):
    output = tmp_path / "output.txt"
    output.touch()
    requirements = CompletionRequirements(
        required_artifacts=("/workspace/output.txt",),
        workspace_root=str(tmp_path),
    )
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "write_file",
            "command": "/workspace/output.txt",
            "output": "wrote 0 bytes",
            "exit_code": 0,
        }],
        requirements,
    )

    assert ledger.evaluate().status == CompletionStatus.BLOCKED


def test_workspace_image_artifact_must_have_valid_file_signature(tmp_path):
    (tmp_path / "score_chart.png").write_text("placeholder")
    requirements = CompletionRequirements(
        required_artifacts=("/workspace/score_chart.png",),
        workspace_root=str(tmp_path),
    )
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "write_file",
            "command": "/workspace/score_chart.png",
            "output": "wrote 11 bytes",
            "exit_code": 0,
        }],
        requirements,
    )

    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.BLOCKED
    assert decision.missing_artifacts == ("/workspace/score_chart.png",)


def test_workspace_artifact_presence_completes_only_after_successful_write(tmp_path):
    (tmp_path / "output.txt").write_text("done\n")
    requirements = CompletionRequirements(
        required_artifacts=("/workspace/output.txt",),
        workspace_root=str(tmp_path),
    )
    ledger = EvidenceLedger.from_tool_events(
        [{
            "round": 1,
            "tool": "write_file",
            "command": "/workspace/output.txt",
            "output": "wrote 5 bytes",
            "exit_code": 0,
        }],
        requirements,
    )

    assert ledger.evaluate().can_complete is True


def test_exhaustion_and_awaiting_user_are_non_completion_states():
    ledger = EvidenceLedger()
    assert ledger.evaluate(exhausted=True).status == CompletionStatus.EXHAUSTED
    assert ledger.evaluate(awaiting_user=True).status == CompletionStatus.AWAITING_USER
