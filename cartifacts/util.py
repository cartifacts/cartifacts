from __future__ import annotations

import os.path
from typing import TYPE_CHECKING, Iterable


if TYPE_CHECKING:
    from mypy_boto3_s3.type_defs import ListObjectsV2OutputTypeDef


def isonlydigits(val: str) -> bool:
    for char in val:
        if char not in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
            return False
    return True


def s3_cp(response: ListObjectsV2OutputTypeDef) -> Iterable[str]:
    def clean(prefix: str) -> str:
        return os.path.basename(prefix.rstrip("/"))

    return map(lambda cp: clean(cp["Prefix"]), response["CommonPrefixes"])


__all__ = ("isonlydigits", "s3_cp")
