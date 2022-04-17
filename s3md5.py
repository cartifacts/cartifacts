#!/usr/bin/env python3

import base64
import hashlib
import sys


def calculate(fileobj):
    md5 = hashlib.md5()
    for chunk in iter(lambda: fileobj.read(1024 * 1024), b""):
        md5.update(chunk)
    digest = md5.digest()
    return base64.b64encode(digest).decode("ascii")


if __name__ == "__main__":
    filenames = sys.argv[1:]
    for filename in filenames:
        with open(filename, mode="rb") as fileobj:
            print(calculate(fileobj))
