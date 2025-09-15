# id_utils.py

def normalize_peer_id(value):
    """
    Normalize Telegram peer/channel/user IDs:
    - Accepts an int (returns it unchanged)
    - Accepts legacy strings like 'u123' or '123' (returns int 123)
    - Raises ValueError for anything else
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("u"):
            s = s[1:]
        if s.isdigit():
            return int(s)
    raise ValueError(f"Unsupported peer id format: {value!r}")
