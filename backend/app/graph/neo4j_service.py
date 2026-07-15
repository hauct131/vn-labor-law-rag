"""Kết nối Neo4j và thực hiện graph expansion."""


class Neo4jService:
    def get_related_articles(
        self,
        article_ids: list[str],
        relation_types: list[str],
        limit: int = 20,
    ) -> list[dict]:
        raise NotImplementedError("Sẽ triển khai ở Ngày 12.")
