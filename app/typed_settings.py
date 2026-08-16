"""How a stored setting's type is described.

Everything else that lived here spoke to a raw sqlite3 connection and is gone:
the application reads and writes settings through the repository, and a second
path that bypassed it was an invitation to use the wrong one.
"""


def value_type(value):
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "str"
    return "json"
