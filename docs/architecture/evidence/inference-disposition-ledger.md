# Inference disposition ledger

Date: 2026-09-11

Characterization of Odysseus production callers of `stream_llm` /
`llm_call*` / `task_llm_call_async` (R11, R14, R15, R25, R26, AE7).

Residual product callers were migrated after U8: agentic leftovers onto
`stream_governed_agent`, bounded leftovers onto `submit_model_job`. The
AST scan now finds only Task 18 held sites and the specialist research
probe. Rows below stay so replacement evidence remains one-to-one.

Odysseus stays the production frontend. OpenHands stays the canonical
agent/conversation/event backend. No second transcript. Task 18 stays held.

Primitives live in `src/llm_core.py`. `src/task_endpoint.py` wraps them as
`task_llm_call_async`. Those definitions are not product callers.

`src/agent_loop.py` and `execute_tool_block` are `held` (Task 18). See
`docs/architecture/evidence/legacy-deletion-hold.md`.

Specialist embeddings, STT, TTS, image generation, and capability probes are
`keep-specialist`, not fake agent conversations.

Skill eval/judge and research query/plan helpers that MCP `skills.invoke` /
`research.invoke` can reach are classified here so nested Odysseus completions
are not unlabeled.

## Disposition key

| disposition | meaning |
|---|---|
| agent-conversation | User-visible or scheduled agent turn. Replacement is OpenHands / `stream_governed_agent`. |
| bounded-job | Typed one-shot product work. Replacement is the U6 model-job worker. Do not delete for topology. |
| deterministic | No model call required. None remaining as an `llm_call` / `stream_llm` caller. |
| keep-specialist | Embeddings, speech, image generation, capability probes. Stay on existing services. |
| delete | Safe to remove after replacement acceptance. None: product behavior is retained (R14). |
| held | Task 18 only. Residual until backup, one switch, clean ledger, old runtime disabled. |

## Callers

| path | symbol | disposition | replacement-or-rationale | notes |
|---|---|---|---|---|
| mcp_servers/email_server.py | _ai_draft_reply_to_email | bounded-job | replacement live: mail draft via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/calendar_routes.py | quick_parse | bounded-job | replacement live: calendar parse via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/chat_helpers.py | auto_name_session | bounded-job | U6 replacement live: heuristic first, worker if insufficient | no `llm_call_async`; `submit_model_job` / `session_title_heuristic` |
| routes/chat_routes.py | chat_endpoint | agent-conversation | U8 replacement live: leftover `POST /api/chat` onto `stream_governed_agent` | no `llm_call_async_with_route_fallback`; `stream_governed_agent` |
| routes/chat_routes.py | stream_rewrite | bounded-job | U6 replacement live: rewrite via model-job worker | no `stream_llm`; `submit_model_job` |
| routes/chat_routes.py | stream_with_save | bounded-job | U6 replacement live: compare panes as parallel bounded jobs | no `stream_llm_with_fallback`; `submit_model_job` + resolved-model provenance |
| routes/document/document_routes.py | ai_fill_annotations | bounded-job | replacement live: document extract/fill via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/document/document_routes.py | ai_tidy_documents | bounded-job | replacement live: document tidy via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/email_helpers.py | _generate_email_summary | bounded-job | replacement live: mail summarize via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/email_helpers.py | _generate_scheduled_email_summary | bounded-job | replacement live: mail summarize via model-job worker | no `task_llm_call_async`; `submit_model_job` |
| routes/email_pollers.py | _auto_summarize_pass_single | bounded-job | replacement live: mail reply/calendar/urgency/summarize via worker | no `task_llm_call_async`; `submit_model_job` |
| routes/email_routes.py | ai_reply | bounded-job | replacement live: mail reply via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/email_routes.py | extract_writing_style | bounded-job | replacement live: style extract via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/email_routes.py | translate_email | bounded-job | replacement live: mail translate via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/history/history_routes.py | compact_session | bounded-job | replacement live: compact via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/memory/memory_routes.py | extract_memory | bounded-job | replacement live: memory extract via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/memory/memory_routes.py | import_memories_from_file | bounded-job | replacement live: memory extract via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/note/note_routes.py | dispatch_reminder | bounded-job | replacement live: reminder synthesis via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/preset_routes.py | expand_character_prompt | bounded-job | replacement live: preset expand via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/session_routes.py | auto_sort_sessions | bounded-job | replacement live: auto-sort via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/session_routes.py | compact_session | bounded-job | replacement live: compact via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/skills_routes.py | _eval_skill_necessity | bounded-job | replacement live: skill judge via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/skills_routes.py | _eval_skill_retrieval_precision | bounded-job | replacement live: skill judge via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/skills_routes.py | _eval_skill_run | bounded-job | replacement live: skill judge via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/skills_routes.py | _improve_skill_md | bounded-job | replacement live: skill rewrite via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/task/task_routes.py | _generate_task_name | bounded-job | replacement live: titles via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/task/task_routes.py | parse_task | bounded-job | replacement live: task parse via model-job worker | no `llm_call*`; `submit_model_job` |
| routes/webhook/webhook_routes.py | sync_chat | agent-conversation | U8 replacement live: authenticated governed conversation; Case 2 provider URL/key rejected | no `llm_call_async`; `stream_governed_agent` |
| services/memory/memory_extractor.py | audit_memories | bounded-job | replacement live: memory extract via model-job worker | no `llm_call*`; `submit_model_job` |
| services/memory/memory_extractor.py | extract_and_store | bounded-job | replacement live: memory extract via model-job worker | no `llm_call*`; `submit_model_job` |
| services/memory/skill_extractor.py | maybe_extract_skill | bounded-job | replacement live: skill extract via model-job worker | no `llm_call*`; `submit_model_job` |
| src/agent_loop.py | _run_verifier_subagent | held | Task 18 residual-until-acceptance | remaining scanned `llm_call_async` inside legacy loop |
| src/agent_loop.py | stream_agent_loop | held | Task 18 residual-until-acceptance | remaining scanned `stream_llm_with_fallback` plus synthesis `llm_call_async` |
| src/agent_tools/model_interaction_tools.py | ask_teacher | agent-conversation | replacement live: teacher takeover via `stream_governed_agent` | no `llm_call*`; OpenHands |
| src/agent_tools/model_interaction_tools.py | chat_with_model | bounded-job | replacement live: one-shot model query via worker | no `llm_call*`; `submit_model_job` |
| src/agent_tools/session_tools.py | send_to_session | agent-conversation | replacement live: OpenHands conversation, no second transcript | no `llm_call*`; `stream_governed_agent` |
| src/ai_interaction.py | do_generate_image | keep-specialist | existing image-generation service; not agent reasoning (R15/AE7) | no `llm_call` primitive |
| src/ai_interaction.py | do_pipeline | bounded-job | replacement live: typed pipeline via model-job worker | no `llm_call*`; `submit_model_job` |
| src/builtin_actions.py | _translate | bounded-job | replacement live: mail translate via model-job worker | no `task_llm_call_async`; `submit_model_job` |
| src/builtin_actions.py | _try_ai_tidy_group | bounded-job | replacement live: memory tidy via model-job worker | no `llm_call*`; `submit_model_job` |
| src/builtin_actions.py | action_check_email_urgency | bounded-job | replacement live: mail urgency via model-job worker | no `llm_call*`; `submit_model_job` |
| src/builtin_actions.py | action_classify_events | bounded-job | replacement live: calendar classify via model-job worker | no `llm_call*`; `submit_model_job` |
| src/builtin_actions.py | action_learn_sender_signatures | bounded-job | replacement live: sender extract via model-job worker | no `llm_call*`; `submit_model_job` |
| src/chat_processor.py | ChatProcessor.build_context_preface | bounded-job | replacement live: search-query extract via model-job worker | no `llm_call*`; `submit_model_job` |
| src/context_compactor.py | maybe_compact | bounded-job | replacement live: compact via model-job worker | no `llm_call*`; `submit_model_job` |
| src/deep_research.py | DeepResearcher._llm | agent-conversation | replacement live: agentic research via `stream_governed_agent` | no `llm_call*`; OpenHands |
| src/document_processor.py | analyze_image_with_vl_result | bounded-job | replacement live: vision extract via model-job worker | no `llm_call*`; `submit_model_job` |
| src/research_handler.py | ResearchHandler._probe_endpoint | keep-specialist | capability probe; stay off agent conversation (R15/AE7) | remaining scanned `llm_call_async` ping |
| src/research_handler.py | ResearchHandler.call_research_service | agent-conversation | U8 replacement live: agentic research onto governed OpenHands | no `llm_call_async`; `stream_governed_agent` |
| src/research_handler.py | ResearchHandler.generate_plan | bounded-job | U8 replacement live: research plan helper via model-job worker | no `llm_call_async`; `submit_model_job` |
| src/research_handler.py | ResearchHandler.synthesize_query | bounded-job | U8 replacement live: research query helper via model-job worker | no `llm_call_async`; `submit_model_job` |
| src/session_actions.py | run_auto_sort | bounded-job | replacement live: auto-sort via model-job worker | no `llm_call*`; `submit_model_job` |
| src/task_scheduler.py | TaskScheduler._execute_llm_task | agent-conversation | replacement live: scheduled fallback via `stream_governed_agent` | no `task_llm_call_async`; OpenHands |
| src/task_scheduler.py | TaskScheduler._run_agent_loop | agent-conversation | replacement live: scheduled grace via `stream_governed_agent` | no `task_llm_call_async`; OpenHands |
| src/teacher_escalation.py | _call_teacher | agent-conversation | replacement live: teacher takeover via `stream_governed_agent` | no `llm_call*`; OpenHands |
| src/teacher_escalation.py | evaluate_turn_llm | bounded-job | replacement live: turn judge via model-job worker | no `llm_call*`; `submit_model_job` |
| src/tool_execution.py | execute_tool_block | held | Task 18 residual-until-acceptance | no `llm_call`; used by legacy loop |
| routes/embedding_routes.py | setup_embedding_routes | keep-specialist | embeddings admin/service; not agent reasoning (R15/AE7) | no `llm_call` primitive |
| routes/stt_routes.py | transcribe_audio | keep-specialist | STT; existing speech service (R15/AE7) | no `llm_call` primitive |
| routes/tts_routes.py | synthesize_speech | keep-specialist | TTS; existing speech service (R15/AE7) | no `llm_call` primitive |

## Counts

Scanned production `llm_call` / `stream_llm` / `task_llm_call_async` callers: 3
(`stream_agent_loop`, `_run_verifier_subagent`, `ResearchHandler._probe_endpoint`).
Other ledger rows are replacement-live extras so AE7 stays one-to-one.

| disposition | scanned callers | extra ledger rows |
|---|---:|---:|
| agent-conversation | 0 | 8 |
| bounded-job | 0 | 43 |
| deterministic | 0 | 0 |
| keep-specialist | 1 | 4 |
| delete | 0 | 0 |
| held | 2 | 1 |

Interactive Chat/Agent, leftover `POST /api/chat`, webhook chat, teacher,
`send_to_session`, deep research, and scheduled-agent leftovers use
`stream_governed_agent`. Bounded leftovers use `submit_model_job`.

Task 18 remains held. Specialist probe remains on `llm_call_async`.
