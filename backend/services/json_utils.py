# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

import json
from decimal import Decimal

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return format(obj, "f")
        return super().default(obj)

def json_dumps(obj, *args, **kwargs):
    if "cls" not in kwargs:
        kwargs["cls"] = DecimalEncoder
    return json.dumps(obj, *args, **kwargs)
