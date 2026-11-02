import json

from src import agent_loop as al


SOLAR_SYSTEM_PROMPT = """The container has the following file:
- /workspace/fixtures/planets.json — solar system configuration

Please read the config file, then generate /workspace/output.html to create an
animated solar system visualization:
1. Show each planet orbiting the sun along its defined orbit path
2. Inner planets should orbit faster than outer planets (follow the period settings)
3. Show orbit paths and planet labels as configured
"""


def test_config_file_prompt_is_not_preempted_as_app_settings():
    assert al._parse_qwen_explicit_admin_request(SOLAR_SYSTEM_PROMPT) is None


def test_cloud_provider_research_does_not_route_to_model_endpoints():
    prompt = (
        "What changed in the project's license? Compare technical compatibility, "
        "investigate major cloud provider support, review the roadmap, and produce "
        "a migration recommendation."
    )
    assert al._is_qwen_explicit_endpoint_list_request(prompt) is False
    assert al._parse_qwen_explicit_admin_request(prompt) is None


def test_explicit_configured_provider_listing_still_routes_to_endpoints():
    assert al._is_qwen_explicit_endpoint_list_request(
        "Show the configured model providers available in this app."
    ) is True


def test_config_file_prompt_routes_files_without_settings_domain():
    intent = al._classify_agent_request(
        [{"role": "user", "content": SOLAR_SYSTEM_PROMPT}],
        SOLAR_SYSTEM_PROMPT,
    )

    assert "files" in intent["domains"]
    assert "settings" not in intent["domains"]


def test_explicit_app_settings_inventory_still_routes_settings():
    prompt = "List current settings without changing them."

    assert al._parse_qwen_explicit_admin_request(prompt) == (
        "manage_settings",
        json.dumps({"action": "list"}),
    )
    assert "settings" in al._classify_agent_request(
        [{"role": "user", "content": prompt}], prompt
    )["domains"]


def test_explicit_app_setting_change_still_routes_settings_domain():
    prompt = "Change my app settings for the default model."

    assert "settings" in al._classify_agent_request(
        [{"role": "user", "content": prompt}], prompt
    )["domains"]
