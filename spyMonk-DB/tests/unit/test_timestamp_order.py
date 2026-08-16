from spymonk_enterprise.time.hybrid_clock import Timestamp


def test_logical_component_breaks_ties():
    a = Timestamp(physical=100, logical=1)
    b = Timestamp(physical=100, logical=2)
    assert b > a
    assert a != b


def test_physical_dominates():
    assert Timestamp(physical=200, logical=0) > Timestamp(physical=100, logical=99)
