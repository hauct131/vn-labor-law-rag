from __future__ import annotations

import logging
import math
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class PairScorer(Protocol):
    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """Return one relevance score per (query, document) pair."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}], got {value}")
    return value


@dataclass(frozen=True)
class ContractReviewRerankerConfig:
    enabled: bool = False
    model_name: str = "BAAI/bge-reranker-v2-m3"
    candidate_k: int = 20
    batch_size: int = 4
    max_length: int = 512
    device: str = "auto"
    normalize_scores: bool = True
    fail_open: bool = True

    @classmethod
    def from_env(cls) -> "ContractReviewRerankerConfig":
        candidate_k = _env_int(
            "CONTRACT_REVIEW_RERANKER_CANDIDATE_K", 20, minimum=4, maximum=100
        )
        return cls(
            enabled=_env_bool("CONTRACT_REVIEW_RERANKER_ENABLED", False),
            model_name=os.getenv(
                "CONTRACT_REVIEW_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"
            ).strip(),
            candidate_k=candidate_k,
            batch_size=_env_int(
                "CONTRACT_REVIEW_RERANKER_BATCH_SIZE", 4, minimum=1, maximum=64
            ),
            max_length=_env_int(
                "CONTRACT_REVIEW_RERANKER_MAX_LENGTH", 512, minimum=64, maximum=8192
            ),
            device=os.getenv("CONTRACT_REVIEW_RERANKER_DEVICE", "auto").strip(),
            normalize_scores=_env_bool(
                "CONTRACT_REVIEW_RERANKER_NORMALIZE_SCORES", True
            ),
            fail_open=_env_bool("CONTRACT_REVIEW_RERANKER_FAIL_OPEN", True),
        )


class HuggingFaceSequenceClassificationScorer:
    """Lazy Hugging Face scorer for multilingual cross-encoder reranking.

    The model is loaded only on the first real scoring request. This keeps
    normal backend startup and V1 tests independent from torch/transformers.
    """

    def __init__(self, config: ContractReviewRerankerConfig) -> None:
        self.config = config
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._torch: Any | None = None
        self._device: str | None = None
        self._load_lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
            except ImportError as exc:  # pragma: no cover - exercised on deployment
                raise RuntimeError(
                    "Cross-encoder dependencies are missing. Install "
                    "requirements-contract-review-reranker.txt."
                ) from exc

            if self.config.device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            else:
                device = self.config.device

            tokenizer = AutoTokenizer.from_pretrained(self.config.model_name)
            model = AutoModelForSequenceClassification.from_pretrained(
                self.config.model_name
            )
            model.eval()
            model.to(device)

            self._torch = torch
            self._tokenizer = tokenizer
            self._model = model
            self._device = device
            logger.info(
                "Loaded Contract Review reranker model=%s device=%s",
                self.config.model_name,
                device,
            )

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        self._ensure_loaded()
        assert self._torch is not None
        assert self._tokenizer is not None
        assert self._model is not None
        assert self._device is not None

        output: list[float] = []
        torch = self._torch
        tokenizer = self._tokenizer
        model = self._model

        for start in range(0, len(pairs), self.config.batch_size):
            batch = list(pairs[start : start + self.config.batch_size])
            queries = [query for query, _document in batch]
            documents = [document for _query, document in batch]
            encoded = tokenizer(
                queries,
                documents,
                padding=True,
                truncation=True,
                max_length=self.config.max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(self._device) for key, value in encoded.items()}
            with torch.no_grad():
                logits = model(**encoded).logits.view(-1)
            scores = logits.detach().float().cpu().tolist()
            if self.config.normalize_scores:
                normalized: list[float] = []
                for raw_score in scores:
                    score = float(raw_score)
                    if score >= 0:
                        z = math.exp(-score)
                        normalized.append(1.0 / (1.0 + z))
                    else:
                        z = math.exp(score)
                        normalized.append(z / (1.0 + z))
                scores = normalized
            output.extend(float(score) for score in scores)
        return output


def _read_value(source: Any, *names: str) -> Any:
    if isinstance(source, Mapping):
        for name in names:
            value = source.get(name)
            if value not in (None, ""):
                return value
        return None
    for name in names:
        value = getattr(source, name, None)
        if value not in (None, ""):
            return value
    return None


def default_article_code(source: Any) -> str:
    value = _read_value(source, "article_code", "law_article_code")
    return str(value or "")


def default_chunk_id(source: Any) -> str:
    value = _read_value(source, "chunk_id", "source_id", "id")
    return str(value or "")


def default_source_text(source: Any) -> str:
    # Prefer the complete evidence body, then common preview/body aliases.
    body = _read_value(
        source,
        "source_text",
        "content",
        "text",
        "content_preview",
        "excerpt",
    )
    title = _read_value(source, "article_title", "title")
    code = default_article_code(source)

    parts: list[str] = []
    if code:
        parts.append(f"Điều {code}")
    if title:
        parts.append(str(title))
    if body:
        parts.append(str(body))
    return " — ".join(parts)


@dataclass(frozen=True)
class RerankedItem:
    original_index: int
    reranker_score: float | None
    source: Any


class ContractReviewCrossEncoderReranker:
    """Two-stage evidence selector used by Contract Review V2.

    Input candidates are supplied by the existing lexical retriever. The
    cross-encoder rescoring is semantic-only; existing deterministic legal
    analysis remains unchanged. After scoring, sources are deduplicated by
    article so the highest-scoring evidence for an article wins.
    """

    def __init__(
        self,
        config: ContractReviewRerankerConfig | None = None,
        *,
        scorer: PairScorer | None = None,
    ) -> None:
        self.config = config or ContractReviewRerankerConfig.from_env()
        self._scorer = scorer

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _get_scorer(self) -> PairScorer:
        if self._scorer is None:
            self._scorer = HuggingFaceSequenceClassificationScorer(self.config)
        return self._scorer

    @staticmethod
    def _baseline_select(
        candidates: Sequence[T],
        *,
        top_k: int,
        article_code_getter: Callable[[T], str],
    ) -> list[T]:
        selected: list[T] = []
        seen: set[str] = set()
        for candidate in candidates:
            article = article_code_getter(candidate)
            dedupe_key = article or f"__candidate_{id(candidate)}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            selected.append(candidate)
            if len(selected) >= top_k:
                break
        return selected

    def rerank_with_scores(
        self,
        *,
        query: str,
        candidates: Sequence[T],
        top_k: int,
        text_getter: Callable[[T], str] = default_source_text,
        article_code_getter: Callable[[T], str] = default_article_code,
        chunk_id_getter: Callable[[T], str] = default_chunk_id,
    ) -> list[RerankedItem]:
        """Return the selected sources together with their CE scores.

        This is an observability API for Contract Review. Selection semantics are
        intentionally identical to :meth:`rerank`. When the reranker is disabled
        or fail-open fallback is used, ``reranker_score`` is ``None`` so callers
        cannot mistake a lexical score for a Cross-Encoder score.
        """
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        if not candidates:
            return []

        def baseline_items() -> list[RerankedItem]:
            selected: list[RerankedItem] = []
            seen: set[str] = set()
            for original_index, candidate in enumerate(candidates):
                article = article_code_getter(candidate)
                dedupe_key = article or f"__candidate_{id(candidate)}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                selected.append(RerankedItem(original_index, None, candidate))
                if len(selected) >= top_k:
                    break
            return selected

        normalized_query = query.strip()
        if not normalized_query or not self.enabled:
            return baseline_items()

        pairs = [(normalized_query, text_getter(candidate)) for candidate in candidates]
        try:
            scores = self._get_scorer().score_pairs(pairs)
            if len(scores) != len(candidates):
                raise RuntimeError(
                    "Reranker returned a different number of scores than candidates"
                )
            if any(not math.isfinite(float(score)) for score in scores):
                raise RuntimeError("Reranker returned a non-finite score")
        except Exception:
            if not self.config.fail_open:
                raise
            logger.exception(
                "Contract Review reranker failed; falling back to lexical order"
            )
            return baseline_items()

        ranked = [
            RerankedItem(i, float(score), candidate)
            for i, (candidate, score) in enumerate(zip(candidates, scores, strict=True))
        ]
        ranked.sort(key=lambda item: (-float(item.reranker_score), item.original_index))

        selected: list[RerankedItem] = []
        seen_articles: set[str] = set()
        for item in ranked:
            article = article_code_getter(item.source)
            chunk = chunk_id_getter(item.source)
            key = article or chunk or f"__candidate_{item.original_index}"
            if key in seen_articles:
                continue
            seen_articles.add(key)
            selected.append(item)
            if len(selected) >= top_k:
                break
        return selected

    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[T],
        top_k: int,
        text_getter: Callable[[T], str] = default_source_text,
        article_code_getter: Callable[[T], str] = default_article_code,
        chunk_id_getter: Callable[[T], str] = default_chunk_id,
    ) -> list[T]:
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        if not candidates:
            return []
        normalized_query = query.strip()
        if not normalized_query or not self.enabled:
            return self._baseline_select(
                candidates,
                top_k=top_k,
                article_code_getter=article_code_getter,
            )

        pairs = [(normalized_query, text_getter(candidate)) for candidate in candidates]
        try:
            scores = self._get_scorer().score_pairs(pairs)
            if len(scores) != len(candidates):
                raise RuntimeError(
                    "Reranker returned a different number of scores than candidates"
                )
            if any(not math.isfinite(float(score)) for score in scores):
                raise RuntimeError("Reranker returned a non-finite score")
        except Exception:
            if not self.config.fail_open:
                raise
            logger.exception(
                "Contract Review reranker failed; falling back to lexical order"
            )
            return self._baseline_select(
                candidates,
                top_k=top_k,
                article_code_getter=article_code_getter,
            )

        ranked = [
            RerankedItem(i, float(score), candidate)
            for i, (candidate, score) in enumerate(zip(candidates, scores, strict=True))
        ]
        # Higher CE score wins. Lexical order is a deterministic tie-breaker.
        ranked.sort(key=lambda item: (-item.reranker_score, item.original_index))

        selected: list[T] = []
        seen_articles: set[str] = set()
        for item in ranked:
            article = article_code_getter(item.source)
            chunk = chunk_id_getter(item.source)
            key = article or chunk or f"__candidate_{item.original_index}"
            if key in seen_articles:
                continue
            seen_articles.add(key)
            selected.append(item.source)
            if len(selected) >= top_k:
                break
        return selected


_singleton_lock = threading.Lock()
_singleton: ContractReviewCrossEncoderReranker | None = None


def get_contract_review_reranker() -> ContractReviewCrossEncoderReranker:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ContractReviewCrossEncoderReranker()
    return _singleton


def reset_contract_review_reranker_for_tests() -> None:
    global _singleton
    with _singleton_lock:
        _singleton = None
