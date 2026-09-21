import pytest

from gleipnir.adapters.cvr import normalise_cvr, valid_cvr_checksum


# Well-formed but not-yet-issued CVR numbers (the 99xxxxxx range). Each passes
# mod-11 under the published weights 2,7,6,5,4,3,2,1 — e.g.
# 99000147: 9·2+9·7+0·6+0·5+0·4+1·3+4·2+7·1 = 99 = 9·11.
VALID = ["99000147", "99000384", "99000368", "99000341", "99000376"]


@pytest.mark.parametrize("cvr", VALID)
def test_well_formed_cvr_numbers_pass(cvr):
    assert valid_cvr_checksum(cvr)


def test_weights_match_a_hand_computed_sum():
    assert sum(int(d) * w for d, w in zip("99000147", (2, 7, 6, 5, 4, 3, 2, 1))) == 99


@pytest.mark.parametrize("cvr", VALID)
def test_single_digit_corruption_is_caught(cvr):
    """Mod-11 catches every single-digit error, which is the failure mode that
    actually happens: a misread column or a truncated cell."""
    caught = 0
    trials = 0
    for pos in range(8):
        for d in "0123456789":
            if d == cvr[pos]:
                continue
            trials += 1
            corrupted = cvr[:pos] + d + cvr[pos + 1:]
            if not valid_cvr_checksum(corrupted):
                caught += 1
    assert caught == trials


def test_normalise_pads_and_strips():
    assert normalise_cvr("12 34 56 78") == "12345678"
    assert normalise_cvr(1234567) == "01234567"
    assert normalise_cvr("DK-99000147") == "99000147"


def test_normalise_rejects_overlong():
    with pytest.raises(ValueError):
        normalise_cvr("123456789")
