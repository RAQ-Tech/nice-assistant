"""The one definition of "now" that rows are stamped with."""

import time


def now_ts() -> int:
    return int(time.time())
