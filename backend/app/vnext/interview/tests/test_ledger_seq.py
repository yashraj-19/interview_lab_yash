"""Ledger seq monotonicity + backfill semantics."""
from app.vnext.interview.ledger import SessionLedger


def _fixed_clock():
    box = {"n": 1_700_000_000_000}

    def clock():
        box["n"] += 1000
        return box["n"]

    return clock


def test_seq_strictly_increasing():
    led = SessionLedger("s1", clock=_fixed_clock())
    seqs = []
    for i in range(5):
        ev = led.append("system", "interviewer.utterance", {"lineId": f"L{i}", "text": "x"})
        seqs.append(ev["seq"])
    assert seqs == [1, 2, 3, 4, 5]
    assert led.last_seq == 5


def test_event_is_flat_envelope():
    led = SessionLedger("s1", clock=_fixed_clock())
    ev = led.append("candidate", "candidate.utterance", {"lineId": "L1", "text": "hi"})
    assert ev["v"] == 1
    assert ev["sessionId"] == "s1"
    assert ev["actor"] == "candidate"
    assert ev["type"] == "candidate.utterance"
    assert ev["lineId"] == "L1" and ev["text"] == "hi"
    assert isinstance(ev["ts"], int)


def test_backfill_returns_only_newer():
    led = SessionLedger("s1", clock=_fixed_clock())
    for i in range(6):
        led.append("system", "interviewer.utterance", {"lineId": f"L{i}", "text": "x"})
    newer = led.backfill(3)
    assert [e["seq"] for e in newer] == [4, 5, 6]
    assert led.backfill(6) == []
    assert [e["seq"] for e in led.backfill(0)] == [1, 2, 3, 4, 5, 6]


def test_find_seq_by_ref():
    led = SessionLedger("s1", clock=_fixed_clock())
    led.append("interviewer", "interviewer.utterance", {"lineId": "L1", "text": "x"})
    led.append("candidate", "code.edited", {"editId": "E1", "after": "code", "by": "candidate"})
    led.append("candidate", "code.run", {"runId": "R1", "code": "c", "stdout": "", "exitCode": 0})
    assert led.find_seq_by_ref("L1") == 1
    assert led.find_seq_by_ref("E1") == 2
    assert led.find_seq_by_ref("R1") == 3
    assert led.find_seq_by_ref("missing") == 0
