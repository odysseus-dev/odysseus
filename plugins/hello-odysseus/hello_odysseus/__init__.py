"""Hello Odysseus — reference plugin backend."""


def register(host):
    """Called by Odysseus at startup."""
    import odysseus

    odysseus.log("info", "Hello from the reference plugin!")

    # Register a simple tool
    host.add_tool(
        "hello_greet",
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Who to greet"},
            },
            "required": ["name"],
        },
        fn=lambda name: f"Hello, {name}! Greetings from hello-odysseus plugin.",
    )

    # Add a settings section
    host.add_settings_section(
        "hello",
        "Hello Plugin",
        render_fn=lambda: "<p>Hello settings rendered by the reference plugin.</p>",
    )
