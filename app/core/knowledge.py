"""Pure concept matching and connection-selection policy shared by readers/workers."""

from collections.abc import Iterable, Sequence
from typing import Protocol, TypeVar

MAX_CONNECTIONS_PER_NOTE = 10


class ConceptBearing(Protocol):
    concepts: list[str]


T = TypeVar("T", bound=ConceptBearing)


def normalized_concepts(concepts: Iterable[str]) -> set[str]:
    return {concept.strip().casefold() for concept in concepts if concept.strip()}


def shared_concepts(left: Iterable[str], right: Iterable[str]) -> list[str]:
    right_normalized = normalized_concepts(right)
    shared: dict[str, str] = {}
    for concept in left:
        normalized = concept.strip().casefold()
        if normalized and normalized in right_normalized:
            shared.setdefault(normalized, concept.strip())
    return sorted(shared.values(), key=str.casefold)


def select_related_notes(concepts: Iterable[str], candidates: Sequence[T]) -> list[T]:
    """Keep strongest overlaps; ties retain the repository's existing ordering."""
    own_concepts = normalized_concepts(concepts)
    related = [
        (len(overlap), candidate)
        for candidate in candidates
        if (overlap := own_concepts & normalized_concepts(candidate.concepts))
    ]
    related.sort(key=lambda item: -item[0])
    return [candidate for _, candidate in related[:MAX_CONNECTIONS_PER_NOTE]]
