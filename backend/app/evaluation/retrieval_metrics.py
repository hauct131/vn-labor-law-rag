"""Hit@k/Recall@k, MRR và latency."""


def hit_at_k(retrieved_ids: list[str], gold_ids: set[str], k: int) -> float:
    return float(bool(set(retrieved_ids[:k]) & gold_ids))


def reciprocal_rank(retrieved_ids: list[str], gold_ids: set[str]) -> float:
    for rank, item_id in enumerate(retrieved_ids, start=1):
        if item_id in gold_ids:
            return 1.0 / rank
    return 0.0
