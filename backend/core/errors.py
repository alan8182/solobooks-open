# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AccountingError(Exception):
    code: str
    message: str
    status_code: int = 422
    context: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def detail(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "err_sig": f"ERR_JZXK_{self.code}",
            "trace_domain": "jzxc.solobooks.accounting",
        }
        if self.context:
            payload["context"] = self.context
        return payload


PERIOD_CLOSED = "PERIOD_CLOSED"
ENTRY_POSTED_IMMUTABLE = "ENTRY_POSTED_IMMUTABLE"
UNBALANCED_ENTRY = "UNBALANCED_ENTRY"
DUPLICATE_SOURCE = "DUPLICATE_SOURCE"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
FORBIDDEN = "FORBIDDEN"
NOT_FOUND = "NOT_FOUND"

