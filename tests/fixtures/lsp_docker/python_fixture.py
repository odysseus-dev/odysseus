def defined_symbol(value: int) -> int:
    return value + 1


def use_defined_symbol() -> int:
    return defined_symbol(41)


def deliberate_python_error() -> int:
    return undefined_symbol
