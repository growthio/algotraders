# -*- coding: utf-8 -*-
"""
Custom Flake8 Plugin: Enforce Custom Blank-Line Rule (BL001, BL002)
-------------------------------------------------------------------

Implements two project-specific blank-line rules that replace the
default Flake8 behavior for E301, E302, E303, and E305 to match the
repository's code formatting conventions.
"""

import ast
from typing import Generator, Tuple

class BlankLinesChecker:
    """
    A custom rule built using :mod:`ast` and :mod:`flake8` to enforce
    blank line rules (BL001, BL002) which are defined as:

        * **BL001**: Enforce a single blank line between an import
            block and the statement that immediately follows it. This
            replaces E302's requirement of two blank lines before a
            top-level function or class definition when those
            definitions appear directly after imports.
        * **BL002**: Enforce exactly two blank lines before every
            function or class definition inside a class body that is
            not the very first body item. This replaces E301's
            single-blank-line requirement and supersedes the E303 cap
            that would flag two blank lines inside a class.

    :NOTE: Extended ``.flake8`` default rule in configuration set to
        ignore ``E301, E302, E303, E305`` so that the built-in rules
        do not conflict with the custom rule BL001 / BL002.

    :type  tree: ast.AST
    :param tree: Get, read and check the Abstract Syntax Tree (AST)
        while running the code to parse the source file.

    :type  lines: list[str]
    :param lines: Source lines of the file with all the contents, and
        each file ending with ``\\n`` escape characters.
    """

    name = "algotraders-flake8"
    version = "0.1.0"

    def __init__(self, tree : ast.AST, lines : list[str]) -> None:
        self.tree = tree
        self.lines = lines


    def run(self) -> Generator[Tuple[int, int, str, type], None, None]:
        """
        Execute all registered checks and yield violation tuples. This
        can then either raise an warning or fail with an error.

        :rtype:  Generator[Tuple[int, int, str, type], None, None]
        :return: Tuples of ``(line, col, message, checker_type)`` for
            every infraction detected.
        """

        yield from self.__check_bl001__()
        yield from self.__check_bl002__()


    def __get_effective_start__(self, node : ast.stmt) -> int:
        """
        Returns the effective first line of a node, accounting for
        the decorators. When a node carries a decorator list the first
        decorator's line number is returned instead of the  ``def`` or
        ``class`` keyword line.

        :type  node: ast.stmt
        :param node: AST node to inspect for the source file during
            inspection.

        :rtype:  int
        :return: One-indexed line number of the node's effective
            starting line.
        """

        return (
            node.decorator_list[0].lineno if
            hasattr(node, "decorator_list") and node.decorator_list
            else node.lineno
        )


    def __count_blanks_between__(
        self,
        from_line : int,
        to_line : int
    ) -> int:
        """
        Count blank lines strictly between two 1-indexed line numbers.

        A blank line is any line whose stripped content is the empty
        string.  Comment lines and lines containing code are not counted.

        :type  from_line: int
        :param from_line: Last occupied line of the preceding node (1-indexed).

        :type  to_line: int
        :param to_line: First occupied line of the following node (1-indexed).

        :rtype:  int
        :return: Number of blank lines in the open interval
                 ``(from_line, to_line)``.
        """
        count = 0
        # 1-indexed range (from_line+1 … to_line-1) maps to
        # 0-indexed array range [from_line … to_line-2]
        for idx in range(from_line, to_line - 1):
            if self.lines[idx].strip() == "":
                count += 1
        return count

    def __is_import__(self, node : ast.stmt) -> bool:
        """
        Checks the source code line, if it is an ``__import__`` line
        statement, and returns a boolean flag.
        """

        return isinstance(node, (ast.Import, ast.ImportFrom))

    # --- rule implementations ---

    def __check_bl001__(
        self
    ) -> Generator[Tuple[int, int, str, type], None, None]:
        """
        Enforces ``BL001` rule that states that there must be exactly
        one blank line after each import block.

        Scans module-level statements for runs of consecutive import
        nodes. For each run that is followed by a non-import statement,
        the blank-line count between the last import and that statement
        must equal exactly 1.  The violation is reported on the line
        immediately after the last import.

        :rtype:  Generator[Tuple[int, int, str, type], None, None]
        :return: BL001 violation tuples.
        """

        body = self.tree.body
        if not body:
            return

        i = 0
        while i < len(body):
            if not self.__is_import__(body[i]):
                i += 1
                continue

            j = i
            while j < len(body) and self.__is_import__(body[j]):
                j += 1

            if j < len(body):
                last_import = body[j - 1]
                next_stmt   = body[j]

                next_start  = self.__get_effective_start__(next_stmt)
                blank_lines = self.__count_blanks_between__(
                    last_import.end_lineno, next_start
                )

                if blank_lines != 1:
                    yield (
                        last_import.end_lineno + 1,
                        0,
                        (
                            f"BL001 expected 1 blank line after import block, "
                            f"found {blank_lines}"
                        ),
                        type(self),
                    )

            i = j if j > i else i + 1


    def __check_bl002__(
        self
    ) -> Generator[Tuple[int, int, str, type], None, None]:
        """
        BL002 — Enforce exactly two blank lines before method and
        nested-class definitions inside a class body.

        Walks the entire AST for class definitions and inspects each
        class body in turn.  Every ``FunctionDef``, ``AsyncFunctionDef``,
        or ``ClassDef`` that is not the first item in the class body must
        be preceded by exactly two blank lines.  The violation is reported
        on the effective start line of the offending definition.

        :rtype:  Generator[Tuple[int, int, str, type], None, None]
        :return: BL002 violation tuples.
        """

        for node in ast.walk(self.tree):
            if not isinstance(node, ast.ClassDef):
                continue

            prev_item = None
            for item in node.body:
                is_def = isinstance(
                    item,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )

                if is_def and prev_item is not None:
                    prev_end    = prev_item.end_lineno
                    item_start  = self.__get_effective_start__(item)
                    blank_lines = self.__count_blanks_between__(
                        prev_end, item_start
                    )

                    if blank_lines != 2:
                        yield (
                            item_start,
                            0,
                            (
                                f"BL002 expected 2 blank lines before "
                                f"function/class definition, found {blank_lines}"
                            ),
                            type(self),
                        )

                prev_item = item
