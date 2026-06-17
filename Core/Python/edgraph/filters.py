class FilterBuilder:
    """Builds OData-style filter strings for EdGraph API query parameters."""

    def __init__(self, filter_str: str | None = None) -> None:
        if not filter_str or filter_str.isspace():
            filter_str = "true"
        self._parts: list[str] = [filter_str]

    def and_(self, filter_str: str | None) -> FilterBuilder:
        if filter_str and not filter_str.isspace():
            self._parts.append(f" && {filter_str}")
        return self

    def build(self) -> str:
        return "".join(self._parts)
