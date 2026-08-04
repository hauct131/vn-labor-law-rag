"""Small offline fallback for LangChain's recursive text splitter.

It is intentionally limited to the ``split_text`` surface used by this
project.  Installed production environments continue to use
``langchain_text_splitters.RecursiveCharacterTextSplitter``.
"""

from __future__ import annotations

from collections.abc import Callable


class RecursiveCharacterTextSplitter:
    def __init__(
        self,
        *,
        chunk_size: int,
        chunk_overlap: int,
        length_function: Callable[[str], int],
        separators: list[str],
        keep_separator: bool = True,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be in [0, chunk_size)")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.length_function = length_function
        self.separators = [value for value in separators if value]
        self.keep_separator = keep_separator

    def _largest_prefix(self, text: str, limit: int) -> int:
        low, high = 1, len(text)
        best = 1
        while low <= high:
            middle = (low + high) // 2
            if self.length_function(text[:middle]) <= limit:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        return best

    def _overlap_start(self, text: str, end: int) -> int:
        if self.chunk_overlap == 0:
            return end
        low, high = 0, end
        best = end
        while low <= high:
            middle = (low + high) // 2
            length = self.length_function(text[middle:end])
            if length <= self.chunk_overlap:
                best = middle
                high = middle - 1
            else:
                low = middle + 1
        return best

    def split_text(self, text: str) -> list[str]:
        remaining = text.strip()
        if not remaining:
            return []
        if self.length_function(remaining) <= self.chunk_size:
            return [remaining]

        chunks: list[str] = []
        cursor = 0
        while cursor < len(remaining):
            tail = remaining[cursor:]
            if self.length_function(tail) <= self.chunk_size:
                chunks.append(tail.strip())
                break

            prefix_length = self._largest_prefix(tail, self.chunk_size)
            split_at = prefix_length
            minimum = max(1, prefix_length // 3)
            for separator in self.separators:
                candidate = tail.rfind(separator, minimum, prefix_length)
                if candidate >= minimum:
                    split_at = (
                        candidate + len(separator)
                        if self.keep_separator
                        else candidate
                    )
                    break

            chunk = tail[:split_at].strip()
            if not chunk:
                split_at = prefix_length
                chunk = tail[:split_at].strip()
            chunks.append(chunk)

            absolute_end = cursor + split_at
            next_cursor = self._overlap_start(remaining, absolute_end)
            if next_cursor <= cursor:
                next_cursor = absolute_end
            cursor = next_cursor

        return [chunk for chunk in chunks if chunk]
