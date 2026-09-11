# Inference disposition ledger

Date: 2026-09-11

Characterization of remaining Odysseus production callers of `stream_llm` /
`llm_call*` / `task_llm_call_async` before any leftover path is removed
(R11, R14, R15, R25, R26, AE7).

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
| mcp_servers/email_server.py | _ai_draft_reply_to_email | bounded-job | U6 model-job worker; keep mail reply | `llm_call_async_with_fallback`; MCP-adjacent draft |
| routes/calendar_routes.py | quick_parse | bounded-job | U6 model-job worker; calendar parse | `llm_call_async` |
| routes/chat_helpers.py | auto_name_session | bounded-job | U6 model-job worker; session titles | `llm_call_async` |
| routes/chat_routes.py | chat_endpoint | agent-conversation | leftover `POST /api/chat`; U8 onto `stream_governed_agent` | `llm_call_async_with_route_fallback` |
| routes/chat_routes.py | stream_rewrite | bounded-job | U6 model-job worker; rewrite | `stream_llm` |
| routes/chat_routes.py | stream_with_save | bounded-job | U6 model-job worker; compare panes | `stream_llm_with_fallback` behind `compare_mode` |
| routes/document/document_routes.py | ai_fill_annotations | bounded-job | U6 model-job worker; document extract/fill | `llm_call_async` VL page fill |
| routes/document/document_routes.py | ai_tidy_documents | bounded-job | U6 model-job worker; document tidy | `llm_call_async` |
| routes/email_helpers.py | _generate_email_summary | bounded-job | U6 model-job worker; mail summarize | `llm_call_async` |
| routes/email_helpers.py | _generate_scheduled_email_summary | bounded-job | U6 model-job worker; mail summarize | `task_llm_call_async` |
| routes/email_pollers.py | _auto_summarize_pass_single | bounded-job | U6 model-job worker; mail reply, calendar extract, urgency, summarize | four `task_llm_call_async` sites |
| routes/email_routes.py | ai_reply | bounded-job | U6 model-job worker; mail reply | `llm_call_async_with_fallback` plus retry `llm_call_async` |
| routes/email_routes.py | extract_writing_style | bounded-job | U6 model-job worker; style extract | `llm_call_async` |
| routes/email_routes.py | translate_email | bounded-job | U6 model-job worker; mail translate | `llm_call_async_with_fallback` |
| routes/history/history_routes.py | compact_session | bounded-job | U6 model-job worker; compact | `llm_call_async` |
| routes/memory/memory_routes.py | extract_memory | bounded-job | U6 model-job worker; memory extract | `llm_call_async` |
| routes/memory/memory_routes.py | import_memories_from_file | bounded-job | U6 model-job worker; memory extract | `llm_call_async` |
| routes/note/note_routes.py | dispatch_reminder | bounded-job | U6 model-job worker; reminder synthesis | `llm_call_async` |
| routes/preset_routes.py | expand_character_prompt | bounded-job | U6 model-job worker; preset expand | `llm_call_async`; live code, not in starting map |
| routes/session_routes.py | auto_sort_sessions | bounded-job | U6 model-job worker; auto-sort | `llm_call` |
| routes/session_routes.py | compact_session | bounded-job | U6 model-job worker; compact | `llm_call_async` |
| routes/skills_routes.py | _eval_skill_necessity | bounded-job | U6 model-job worker; skill judge | `llm_call_async`; MCP-adjacent `skills.invoke` |
| routes/skills_routes.py | _eval_skill_retrieval_precision | bounded-job | U6 model-job worker; skill judge | `llm_call_async`; MCP-adjacent `skills.invoke` |
| routes/skills_routes.py | _eval_skill_run | bounded-job | U6 model-job worker; skill judge | `llm_call_async`; MCP-adjacent `skills.invoke` |
| routes/skills_routes.py | _improve_skill_md | bounded-job | U6 model-job worker; skill rewrite | `llm_call_async`; MCP-adjacent `skills.invoke` |
| routes/task/task_routes.py | _generate_task_name | bounded-job | U6 model-job worker; titles | `llm_call_async` |
| routes/task/task_routes.py | parse_task | bounded-job | U6 model-job worker; task parse | `llm_call_async` |
| routes/webhook/webhook_routes.py | sync_chat | agent-conversation | leftover `POST /api/v1/chat`; U8 governed conversation, never completions gateway | `llm_call_async` |
| services/memory/memory_extractor.py | audit_memories | bounded-job | U6 model-job worker; memory extract | `llm_call_async` |
| services/memory/memory_extractor.py | extract_and_store | bounded-job | U6 model-job worker; memory extract | `llm_call_async` |
| services/memory/skill_extractor.py | maybe_extract_skill | bounded-job | U6 model-job worker; skill extract | `llm_call_async` |
| src/agent_loop.py | _run_verifier_subagent | held | Task 18 residual-until-acceptance | `llm_call_async` inside legacy loop |
| src/agent_loop.py | stream_agent_loop | held | Task 18 residual-until-acceptance | `stream_llm_with_fallback` plus synthesis `llm_call_async` |
| src/agent_tools/model_interaction_tools.py | ask_teacher | agent-conversation | teacher takeover via tool; OpenHands teacher path | `llm_call_async` |
| src/agent_tools/model_interaction_tools.py | chat_with_model | bounded-job | U6 model-job worker; one-shot model query, no durable transcript | `llm_call_async` |
| src/agent_tools/session_tools.py | send_to_session | agent-conversation | leftover competing session transcript; OpenHands conversation | `llm_call_async` |
| src/ai_interaction.py | do_generate_image | keep-specialist | existing image-generation service; not agent reasoning (R15/AE7) | no `llm_call` primitive |
| src/ai_interaction.py | do_pipeline | bounded-job | U6 model-job worker; typed multi-step pipeline | `llm_call_async` |
| src/builtin_actions.py | _translate | bounded-job | U6 model-job worker; mail translate | `task_llm_call_async` |
| src/builtin_actions.py | _try_ai_tidy_group | bounded-job | U6 model-job worker; memory tidy | `llm_call_async_with_fallback` |
| src/builtin_actions.py | action_check_email_urgency | bounded-job | U6 model-job worker; mail urgency | `llm_call_async_with_fallback` |
| src/builtin_actions.py | action_classify_events | bounded-job | U6 model-job worker; calendar classify | `llm_call_async_with_fallback` |
| src/builtin_actions.py | action_learn_sender_signatures | bounded-job | U6 model-job worker; sender extract | `llm_call_async_with_fallback` |
| src/chat_processor.py | ChatProcessor.build_context_preface | bounded-job | U6 model-job worker; search-query extract | `llm_call` |
| src/context_compactor.py | maybe_compact | bounded-job | U6 model-job worker; compact | `llm_call_async` |
| src/deep_research.py | DeepResearcher._llm | agent-conversation | agentic research; OpenHands/Hermes after U4 | `llm_call_async` |
| src/document_processor.py | analyze_image_with_vl_result | bounded-job | U6 model-job worker; vision extract, not image generation | `llm_call` |
| src/research_handler.py | ResearchHandler._probe_endpoint | keep-specialist | capability probe; stay off agent conversation (R15/AE7) | `llm_call_async` ping |
| src/research_handler.py | ResearchHandler.generate_plan | bounded-job | U6 model-job worker; research plan helper | `llm_call_async`; MCP-adjacent `research.invoke` |
| src/research_handler.py | ResearchHandler.synthesize_query | bounded-job | U6 model-job worker; research query helper | `llm_call_async`; MCP-adjacent `research.invoke` |
| src/session_actions.py | run_auto_sort | bounded-job | U6 model-job worker; auto-sort | `llm_call_async` |
| src/task_scheduler.py | TaskScheduler._execute_llm_task | agent-conversation | scheduled agent fallback after loop raise; stay agent conversation | `task_llm_call_async` |
| src/task_scheduler.py | TaskScheduler._run_agent_loop | agent-conversation | grace summary on scheduled agent path; leftover nested completion | `task_llm_call_async` |
| src/teacher_escalation.py | _call_teacher | agent-conversation | teacher takeover; OpenHands teacher path | `llm_call_async` |
| src/teacher_escalation.py | evaluate_turn_llm | bounded-job | U6 model-job worker; turn judge, not takeover | `llm_call_async` |
| src/tool_execution.py | execute_tool_block | held | Task 18 residual-until-acceptance | no `llm_call`; used by legacy loop |
| routes/embedding_routes.py | setup_embedding_routes | keep-specialist | embeddings admin/service; not agent reasoning (R15/AE7) | no `llm_call` primitive |
| routes/stt_routes.py | transcribe_audio | keep-specialist | STT; existing speech service (R15/AE7) | no `llm_call` primitive |
| routes/tts_routes.py | synthesize_speech | keep-specialist | TTS; existing speech service (R15/AE7) | no `llm_call` primitive |

## Counts

Scanned production `llm_call` / `stream_llm` / `task_llm_call_async` callers: 54
(plus 5 specialist-or-held rows that are not those primitives).

| disposition | scanned callers | extra ledger rows |
|---|---:|---:|
| agent-conversation | 8 | 0 |
| bounded-job | 43 | 0 |
| deterministic | 0 | 0 |
| keep-specialist | 1 | 4 |
| delete | 0 | 0 |
| held | 2 | 1 |

Interactive Chat/Agent, skill tests, and background follow-ups already use
`stream_governed_agent` and are not remaining `llm_call` callers.

No product behavior was removed in this characterization unit.
