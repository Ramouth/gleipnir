from gleipnir.nameline import compare, core_tokens


def test_subsidiary_named_after_its_parent_is_continuous():
    """XR-Turbo International ApS / Eastport XR-TURBO Corp. — the Danish entity
    announces the relationship rather than obscuring it."""
    link = compare("XR-Turbo International ApS", "Eastport XR-TURBO Corp.")
    assert link.continuous
    assert link.shared == {"xr", "turbo"}


def test_geographic_qualifier_does_not_break_the_match():
    assert compare("MARLOG Denmark A/S", "MARLOG AS").continuous
    assert compare("Kabelco ApS", "Kabelco Group AB").continuous


def test_private_equity_vehicle_shares_nothing_and_that_is_not_a_flag():
    """Pharmaco Denmark ApS / Bidco 7 (Luxembourg) Acquisition S.à.r.l. — a PE
    vehicle is named for the deal, not the business. FALSE here is absence of
    evidence, never evidence of concealment."""
    link = compare("Pharmaco Denmark ApS", "Bidco 7 (Luxembourg) Acquisition S.à.r.l.")
    assert not link.continuous and not link.partial
    assert link.shared == frozenset()


def test_noise_words_alone_do_not_create_a_match():
    """Two unrelated companies both called '... Holding Denmark ApS' must not
    read as a group."""
    assert not compare("Bageren Holding Denmark ApS",
                       "Mureren Holding Danmark ApS").continuous


def test_danish_letters_survive_tokenisation():
    """æ/ø/å do not decompose under NFKD — they are distinct letters. An
    ASCII-only split silently ate them: 'Ærø' became 'r', so every Danish name
    containing one was unmatchable."""
    assert core_tokens("Ærø Holding ApS") == {"aeroe"}
    assert compare("Ærø Eksempel ApS", "Aeroe Eksempel AB").continuous
