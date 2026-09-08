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
