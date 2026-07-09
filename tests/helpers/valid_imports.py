import re

def assert_valid_imports(
    renderer: str,
    import_names: list[str],
    module_path: str,
):
    # 1. Remove line comments and block comments
    uncommented = re.sub(r'(//[^\n]*|/\*[\s\S]*?\*/)', '', renderer)

    # 2. Match named import blocks from the module
    #    Supports .js/.ts/.jsx/.tsx extensions
    import_pattern = re.compile(
        rf'import\s*\{{([^}}]+)\}}\s*from\s*[\'"]{re.escape(module_path)}(?:\.[jt]sx?)?[\'"]',
        re.S
    )

    matches = list(import_pattern.finditer(uncommented))
    assert matches, f"No import found from {module_path}"

    # 3. Check each import block for required names
    found = {name: False for name in import_names}

    for match in matches:
        # Extract the contents between { ... }
        imports = [
            imp.strip()
            for imp in match.group(1).split(',')
            if imp.strip()
        ]

        for name in import_names:
            # Reject renaming: "name as something"
            if any(imp.startswith(f"{name} as ") for imp in imports):
                raise AssertionError(
                    f"Import '{name}' is renamed, which is not allowed"
                )

            # Accept only exact matches
            if name in imports:
                found[name] = True

    # 4. Ensure all required imports were found
    missing = [name for name, ok in found.items() if not ok]
    assert not missing, (
        f"Missing required imports from {module_path}: {', '.join(missing)}"
    )