"""Parse all records but select exactly indices 0,1,2. No solver calls here."""
import hashlib


def digest(data):
    return hashlib.sha256(data).hexdigest()


def selected_levels(raw):
    records = []
    label = None
    rows = []
    for line in raw.decode("utf-8").splitlines():
        if line.startswith(";"):
            if label is not None:
                records.append((label, rows))
            label, rows = line[1:].strip(), []
        elif line != "":
            if label is None:
                raise ValueError("board before label")
            rows.append(line)
    if label is not None:
        records.append((label, rows))
    if len(records) < 3:
        raise ValueError("fewer than three records")
    result = []
    for index, (label, rows) in enumerate(records[:3]):
        if len(rows) != 10 or any(len(row) != 10 for row in rows):
            raise ValueError("selected board not 10x10; no substitution")
        result.append({"index": index, "record_id": label, "rows": rows,
                       "board_sha256": digest(("\n".join(rows) + "\n").encode())})
    return result

