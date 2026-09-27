from typing import TypedDict

class Token(TypedDict):
    category: str
    length: int
    offset: int
    text: str
    type: int

class FormatError(Exception): ...

class Syntaqlite:
    def format_sql(
        self,
        sql: str,
        *,
        line_width: int = 80,
        indent_width: int = 2,
        keyword_case: str = "upper",
        semicolons: bool = True,
    ) -> str: ...
    def tokenize(self, sql: str) -> list[Token]: ...
