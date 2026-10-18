"""Minimal streaming reader for MySQL dumps (CREATE TABLE + extended INSERTs)."""
import re
from typing import Iterator

_CREATE = re.compile(r"CREATE TABLE `(\w+)` \((.*?)\n\)", re.S)
_INSERT = re.compile(r"insert\s+into\s+`(\w+)`(?:\s*\(([^)]*)\))?\s+values\s*", re.I)
_ESC = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "Z": "\x1a"}


def parse_values(v: str) -> Iterator[list]:
    """Yield rows from the VALUES part of an extended INSERT; NULL -> None."""
    row, cur, inq, depth, quoted = [], [], False, 0, False
    i, n = 0, len(v)
    while i < n:
        c = v[i]
        if inq:
            if c == "\\" and i + 1 < n:
                nxt = v[i + 1]
                cur.append(_ESC.get(nxt, nxt))
                i += 2
                continue
            if c == "'":
                if i + 1 < n and v[i + 1] == "'":
                    cur.append("'")
                    i += 2
                    continue
                inq = False
            else:
                cur.append(c)
        elif c == "'":
            inq, quoted = True, True
        elif c == "(" and depth == 0:
            depth, row, cur, quoted = 1, [], [], False
        elif c == "," and depth == 1:
            row.append(_val(cur, quoted))
            cur, quoted = [], False
        elif c == ")" and depth == 1:
            row.append(_val(cur, quoted))
            yield row
            depth = 0
        elif depth == 1 and not c.isspace():
            cur.append(c)
        i += 1


def _val(cur, quoted):
    s = "".join(cur)
    if not quoted and s.upper() == "NULL":
        return None
    return s


def table_columns(sql: str) -> dict:
    return {m.group(1): re.findall(r"^\s+`([^`]+)`", m.group(2), re.M) for m in _CREATE.finditer(sql)}


def iter_inserts(lines) -> Iterator[tuple]:
    """Iterate (table, explicit_columns_or_None, values_str) over dump lines."""
    for line in lines:
        m = _INSERT.match(line)
        if m:
            cols = [c.strip(" `") for c in m.group(2).split(",")] if m.group(2) else None
            yield m.group(1), cols, line[m.end():].rstrip().rstrip(";")
