"""Tests for Phase 5: Problem-Spec Engine."""
from __future__ import annotations

import pytest

from app.vnext.interview.problem_spec import (
    get_problem,
    list_problems,
    get_problem_for_role,
    get_hint_for_problem,
    PROBLEM_CATALOG,
)


class TestProblemCatalog:
    """Test problem catalog and spec retrieval."""

    def test_catalog_has_6_problems(self):
        """Catalog: 6 foundational problems."""
        assert len(PROBLEM_CATALOG) == 6

    def test_problem_ids_valid(self):
        """All problems have valid IDs."""
        expected_ids = {
            "two_sum",
            "valid_parentheses",
            "merge_sorted_arrays",
            "reverse_linked_list",
            "binary_search",
            "longest_substring_without_repeating",
        }
        assert set(PROBLEM_CATALOG.keys()) == expected_ids

    def test_get_problem_by_id(self):
        """Get problem by ID."""
        problem = get_problem("two_sum")
        assert problem is not None
        assert problem.title == "Two Sum"
        assert problem.difficulty == "easy"

    def test_get_nonexistent_problem(self):
        """Get nonexistent problem returns None."""
        assert get_problem("nonexistent") is None

    def test_problem_spec_complete(self):
        """Each problem has required fields."""
        for spec in PROBLEM_CATALOG.values():
            assert spec.id
            assert spec.title
            assert spec.difficulty in ("easy", "medium", "hard")
            assert spec.description
            assert spec.function_signature
            assert len(spec.constraints) > 0
            assert len(spec.test_cases) > 0
            assert len(spec.hints) >= 3  # escalation levels 1, 2, 3
            assert spec.time_complexity
            assert spec.space_complexity


class TestListProblems:
    """Test problem filtering and listing."""

    def test_list_all_problems(self):
        """List all problems."""
        all_probs = list_problems()
        assert len(all_probs) == 6

    def test_list_problems_by_difficulty(self):
        """Filter by difficulty."""
        easy = list_problems(difficulty="easy")
        assert all(p.difficulty == "easy" for p in easy)
        assert len(easy) == 3  # two_sum, valid_parentheses, merge_sorted_arrays

        medium = list_problems(difficulty="medium")
        assert all(p.difficulty == "medium" for p in medium)
        assert len(medium) == 3  # reverse_linked_list, binary_search, longest_substring

    def test_list_problems_empty_filter(self):
        """Filter for nonexistent difficulty."""
        hard = list_problems(difficulty="hard")
        assert len(hard) == 0


class TestProblemSelection:
    """Test problem suggestion by role/seniority."""

    def test_junior_sde_gets_easy(self):
        """Junior SDE: suggested easy problem."""
        problem_id = get_problem_for_role("SDE", "junior")
        assert problem_id is not None
        problem = get_problem(problem_id)
        assert problem.difficulty == "easy"

    def test_mid_sde_gets_medium(self):
        """Mid-level SDE: suggested medium problem."""
        problem_id = get_problem_for_role("SDE", "mid")
        assert problem_id is not None
        problem = get_problem(problem_id)
        assert problem.difficulty in ("easy", "medium")

    def test_senior_sde_gets_harder(self):
        """Senior SDE: suggested harder problem."""
        problem_id = get_problem_for_role("SDE", "senior")
        assert problem_id is not None
        problem = get_problem(problem_id)
        # Senior gets medium or hard
        assert problem.difficulty in ("medium", "hard")


class TestHintEscalation:
    """Test hint escalation per problem."""

    def test_hint_level_1_nudge(self):
        """Hint level 1: nudge."""
        hint = get_hint_for_problem("two_sum", 1)
        assert hint is not None
        assert "hash map" in hint.lower() or "data structure" in hint.lower()

    def test_hint_level_2_hint(self):
        """Hint level 2: stronger hint."""
        hint = get_hint_for_problem("two_sum", 2)
        assert hint is not None
        assert "hash map" in hint.lower()

    def test_hint_level_3_reveal(self):
        """Hint level 3: reveal."""
        hint = get_hint_for_problem("two_sum", 3)
        assert hint is not None
        assert "iterate" in hint.lower() or "loop" in hint.lower()

    def test_hint_nonexistent_problem(self):
        """Hint for nonexistent problem."""
        assert get_hint_for_problem("nonexistent", 1) is None

    def test_hint_clamped_to_max_level(self):
        """Hint level > 3 clamped to max."""
        hint_3 = get_hint_for_problem("two_sum", 3)
        hint_10 = get_hint_for_problem("two_sum", 10)
        assert hint_3 == hint_10


class TestTestCases:
    """Test cases embedded in problem specs."""

    def test_two_sum_test_cases(self):
        """Two-sum test cases are correct."""
        spec = get_problem("two_sum")
        assert len(spec.test_cases) >= 3
        # First test case: [2, 7, 11, 15], target 9 -> [0, 1]
        tc = spec.test_cases[0]
        assert tc.input_args["nums"] == [2, 7, 11, 15]
        assert tc.input_args["target"] == 9
        assert tc.expected_output == [0, 1]

    def test_valid_parentheses_test_cases(self):
        """Valid parentheses test cases."""
        spec = get_problem("valid_parentheses")
        assert len(spec.test_cases) >= 4
        # Should include passing and failing cases
        passing = [tc for tc in spec.test_cases if tc.expected_output is True]
        failing = [tc for tc in spec.test_cases if tc.expected_output is False]
        assert len(passing) > 0
        assert len(failing) > 0

    def test_binary_search_test_cases(self):
        """Binary search test cases."""
        spec = get_problem("binary_search")
        # Should include target found and not found
        tc_found = spec.test_cases[0]
        assert tc_found.expected_output >= 0
        tc_not_found = spec.test_cases[1]
        assert tc_not_found.expected_output == -1


class TestComplexityAnnotations:
    """Test complexity annotations."""

    def test_complexity_valid_notation(self):
        """All problems have valid O() notation."""
        valid_bigos = {"O(1)", "O(n)", "O(log n)", "O(n log n)", "O(n^2)", "O(2^n)", "O(min(n, m))"}
        for spec in PROBLEM_CATALOG.values():
            assert spec.time_complexity in valid_bigos or "O(" in spec.time_complexity
            assert spec.space_complexity in valid_bigos or "O(" in spec.space_complexity

    def test_time_space_tradeoffs(self):
        """Verify reasonable time/space tradeoffs."""
        # Two-sum: O(n) time, O(n) space (hash map)
        two_sum = get_problem("two_sum")
        assert "O(n)" in two_sum.time_complexity
        assert "O(n)" in two_sum.space_complexity

        # Merge: O(n+m) time, O(n+m) space (new array)
        merge = get_problem("merge_sorted_arrays")
        assert "O(n" in merge.time_complexity  # O(n + m)
        assert "O(n" in merge.space_complexity


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
