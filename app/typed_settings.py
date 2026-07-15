import json
import time


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


def parse_legacy_preferences(raw):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def load_typed_preferences(conn, user_id, legacy_json="{}"):
    rows = conn.execute(
        "SELECT key, value_json FROM setting_values WHERE user_id=? ORDER BY key",
        (user_id,),
    ).fetchall()
    if not rows:
        return parse_legacy_preferences(legacy_json)
    result = {}
    for row in rows:
        try:
            result[row[0]] = json.loads(row[1])
        except (TypeError, ValueError):
            continue
    return result


def store_typed_preferences(conn, user_id, preferences, updated_at=None):
    values = preferences if isinstance(preferences, dict) else parse_legacy_preferences(preferences)
    stamp = int(updated_at or time.time())
    conn.execute("DELETE FROM setting_values WHERE user_id=?", (user_id,))
    for key, value in sorted(values.items()):
        conn.execute(
            "INSERT INTO setting_values(user_id,key,value_type,value_json,updated_at) VALUES(?,?,?,?,?)",
            (user_id, str(key)[:120], value_type(value), json.dumps(value, separators=(",", ":")), stamp),
        )
    return values
