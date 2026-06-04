"""Guard the browser i18n helpers through Node's ES module loader."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_I18N = _REPO / "static" / "js" / "i18n.js"
_HAS_NODE = shutil.which("node") is not None


def _run_js(source):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_translate_uses_chinese_dictionary_and_english_fallback():
    js = f"""
    import {{ translate }} from '{_I18N.as_posix()}';
    console.log(JSON.stringify([
      translate('zh', 'Settings'),
      translate('en', 'Settings'),
      translate('zh', 'A phrase Odysseus does not know yet')
    ]));
    """
    assert json.loads(_run_js(js)) == [
        "设置",
        "Settings",
        "A phrase Odysseus does not know yet",
    ]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_language_normalization():
    js = f"""
    import {{ normalizeLanguage }} from '{_I18N.as_posix()}';
    console.log(JSON.stringify([
      normalizeLanguage('zh-CN'),
      normalizeLanguage('en'),
      normalizeLanguage('fr')
    ]));
    """
    assert json.loads(_run_js(js)) == ["zh", "en", "en"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_skip_selector_protects_user_content_zones():
    js = f"""
    import {{ shouldSkipElementForI18n }} from '{_I18N.as_posix()}';
    const textarea = {{ nodeType: 1, closest: (selector) => selector.includes('textarea') }};
    const message = {{ nodeType: 1, closest: (selector) => selector.includes('.msg') }};
    const button = {{ nodeType: 1, closest: () => null }};
    const option = {{ nodeType: 1, closest: () => null }};
    console.log(JSON.stringify([
      shouldSkipElementForI18n(textarea),
      shouldSkipElementForI18n(message),
      shouldSkipElementForI18n(button),
      shouldSkipElementForI18n(option)
    ]));
    """
    assert json.loads(_run_js(js)) == [True, True, False, False]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_attribute_translation_allows_form_hints_but_skips_user_content():
    js = f"""
    import {{ shouldSkipAttributesForI18n }} from '{_I18N.as_posix()}';
    const input = {{ nodeType: 1, closest: () => null, matches: () => false }};
    const messageInput = {{
      nodeType: 1,
      closest: (selector) => selector.includes('.msg'),
      matches: () => false
    }};
    const code = {{ nodeType: 1, closest: () => null, matches: (selector) => selector.includes('code') }};
    console.log(JSON.stringify([
      shouldSkipAttributesForI18n(input),
      shouldSkipAttributesForI18n(messageInput),
      shouldSkipAttributesForI18n(code)
    ]));
    """
    assert json.loads(_run_js(js)) == [False, True, True]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_dictionary_covers_more_static_ui_surfaces():
    js = f"""
    import {{ translate }} from '{_I18N.as_posix()}';
    console.log(JSON.stringify([
      translate('zh', 'Add Models'),
      translate('zh', 'Email Accounts'),
      translate('zh', 'Enter 6-digit code'),
      translate('zh', 'No cached models found')
    ]));
    """
    assert json.loads(_run_js(js)) == [
        "添加模型",
        "邮件账户",
        "输入 6 位验证码",
        "未找到缓存模型",
    ]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_research_panel_strings_are_translated():
    js = f"""
    import {{ translate }} from '{_I18N.as_posix()}';
    console.log(JSON.stringify([
      translate('zh', 'Multi-step web research with an LLM-in-the-loop agent'),
      translate('zh', 'Library, Research'),
      translate('zh', 'Auto'),
      translate('zh', '0 research')
    ]));
    """
    assert json.loads(_run_js(js)) == [
        "由大模型参与的多步网页研究代理",
        "文档库，研究",
        "自动",
        "0 项研究",
    ]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_tasks_panel_strings_are_translated():
    js = f"""
    import {{ translate }} from '{_I18N.as_posix()}';
    console.log(JSON.stringify([
      translate('zh', 'Ongoing Tasks'),
      translate('zh', 'Scheduled prompts and actions that run automatically. Results appear in a dedicated session.'),
      translate('zh', 'Calendar Classify Events'),
      translate('zh', 'all (10)'),
      translate('zh', 'email (3)'),
      translate('zh', 'PAUSED'),
      translate('zh', 'Prompt on schedule'),
      translate('zh', 'Recent task runs across all scheduled tasks.')
    ]));
    """
    assert json.loads(_run_js(js)) == [
        "进行中的任务",
        "自动运行的计划提示词和操作。结果会出现在专用会话中。",
        "日历事件分类",
        "全部（10）",
        "邮件（3）",
        "已暂停",
        "按计划运行提示词",
        "所有计划任务的最近运行记录。",
    ]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_settings_shortcuts_strings_are_translated():
    js = f"""
    import {{ translate }} from '{_I18N.as_posix()}';
    console.log(JSON.stringify([
      translate('zh', 'Keyboard Shortcuts'),
      translate('zh', 'Click a shortcut to rebind. Press Escape to cancel.'),
      translate('zh', 'Navigation'),
      translate('zh', 'Search conversations'),
      translate('zh', 'Toggle Window'),
      translate('zh', 'Open Deep Research'),
      translate('zh', 'Press keys...'),
      translate('zh', 'Shortcuts reset to defaults')
    ]));
    """
    assert json.loads(_run_js(js)) == [
        "键盘快捷键",
        "点击快捷键即可重新绑定。按 Escape 取消。",
        "导航",
        "搜索对话",
        "切换窗口",
        "打开深度研究",
        "请按快捷键...",
        "快捷键已重置为默认值",
    ]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_admin_tools_teacher_and_task_event_strings_are_translated():
    js = f"""
    import {{ translate }} from '{_I18N.as_posix()}';
    console.log(JSON.stringify([
      translate('zh', 'Generate Image'),
      translate('zh', 'Create images via AI'),
      translate('zh', 'Knowledge'),
      translate('zh', 'Ask Teacher'),
      translate('zh', 'Query a more capable model'),
      translate('zh', 'Teacher Model'),
      translate('zh', 'Unlimited'),
      translate('zh', 'Documents Tidy'),
      translate('zh', 'Every 5 document createds'),
      translate('zh', 'Limit: 3 tool calls per message')
    ]));
    """
    assert json.loads(_run_js(js)) == [
        "生成图像",
        "通过 AI 创建图像",
        "知识",
        "询问教师模型",
        "查询更强的模型",
        "教师模型",
        "无限制",
        "文档整理",
        "每 5 次 文档创建",
        "限制：每条消息 3 次工具调用",
    ]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_mutation_observer_does_not_watch_translated_attributes():
    js = f"""
    import {{ MUTATION_OBSERVER_OPTIONS }} from '{_I18N.as_posix()}';
    console.log(JSON.stringify(MUTATION_OBSERVER_OPTIONS));
    """
    options = json.loads(_run_js(js))
    assert options["childList"] is True
    assert options["subtree"] is True
    assert "attributes" not in options
    assert "attributeFilter" not in options
