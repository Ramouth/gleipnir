"""Does an atom's own quote carry its statement? A small-model second check.

The atomiser writes time into every statement ("by 2024") and resolves
references, so a raw entailment test fails on exactly what the contract adds.
The hypothesis is therefore the claim with its time phrase removed, and the
premise is the quote alone: the words the atom says it rests on.

The classifier is pinned and offline, but it is still a statistical model. Its
answer is a flag for review ("open"), never a defect and never a verdict. On
the 2026-09-22 pilot it flagged 52 of 123 real atoms. A hand check of 12 found
2 real errors the regex tripwires missed, 6 statements that used context
outside their quote (a scope leak), and 4 classifier mistakes.
"""
from __future__ import annotations

import re
from typing import Protocol

from gleipnir.atomiser import Atom

TIME_PHRASE = re.compile(
    r',?\s*\b(as of|by|in|during|since|as (reported|stated|described) in)\s+'
    r'(\d{4}(-\d{2}){0,2}|\d{1,2} \w+ \d{4})\b,?|\s*\(\d{4}\)', re.I)


class Classifier(Protocol):
    def classify(self, premise: str, hypothesis: str) -> dict[str, float]: ...


def hypothesis(atom: Atom) -> str:
    """The claim as the quote should state it: without the time we bound."""
    text = TIME_PHRASE.sub('', atom.chain()[1].statement).strip(' ,.')
    return (text[:1].upper() + text[1:] + '.') if text else ''


def check_support(atom: Atom, classifier: Classifier) -> dict:
    probabilities = classifier.classify(atom.quote, hypothesis(atom))
    label = max(probabilities, key=probabilities.get)
    return {'label': label, 'entailment': probabilities['entailment'],
            'flag': None if label == 'entailment' else f'quote_{label}'}


def review_request(atom: Atom, support: dict) -> dict | None:
    """What the LLM is asked when the classifier flags an atom, or None.

    A flag is a question, never a sign-off. The request carries the quote, the
    statement and a neutral question, and nothing of the classifier's label,
    score or confidence: the LLM must reread the source, not defer to a smaller
    model. An unflagged atom produces no request at all, so the absence of a
    flag never reaches the LLM as approval. Whatever the LLM answers is a new
    proposal, checked by atomiser.check() like any other.
    """
    if not support.get('flag'):
        return None
    return {'quote': atom.quote, 'statement': atom.chain()[1].statement,
            'question': 'Does the quote itself state this? If it does, name the words that '
                        'state it. If it does not, rewrite the atom so that it says only what '
                        'the quote says, or quote the further words it rests on.'}
