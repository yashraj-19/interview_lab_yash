"""Phase 5: Problem-Spec Engine — dynamic problem definitions for SDE interviews.

Provides pluggable problem catalog with:
- Function signature and constraints
- Example test cases (input/output pairs)
- Solution hints (never-reveal in interview)
- Complexity expectations (time/space)
- Difficulty levels (easy, medium, hard)

Currently includes 6 foundational problems; extend by adding to PROBLEM_CATALOG.
"""
from __future__ import annotations

from typing import Any, Callable, Optional
from dataclasses import dataclass


@dataclass
class TestCase:
    """Single test case: input -> expected output."""
    input_args: dict[str, Any]
    expected_output: Any
    description: str = ""


@dataclass
class ProblemSpec:
    """Complete problem specification."""
    id: str
    title: str
    difficulty: str  # "easy", "medium", "hard"
    description: str
    function_signature: str
    constraints: list[str]
    test_cases: list[TestCase]
    hints: list[str]  # escalating hints (nudge -> hint -> reveal)
    time_complexity: str  # e.g. "O(n)", "O(n log n)"
    space_complexity: str  # e.g. "O(1)", "O(n)"


# ─────────────────────────────────────────────────────────────────────────────
# PROBLEM CATALOG: 6 FOUNDATIONAL SDE PROBLEMS
# ─────────────────────────────────────────────────────────────────────────────

PROBLEM_CATALOG: dict[str, ProblemSpec] = {
    "two_sum": ProblemSpec(
        id="two_sum",
        title="Two Sum",
        difficulty="easy",
        description="Given an array of integers and a target sum, return the indices of the two numbers that add up to the target.",
        function_signature="def two_sum(nums: list[int], target: int) -> list[int]:",
        constraints=[
            "Each input has exactly one solution.",
            "You may not use the same element twice.",
            "You can return the answer in any order.",
            "2 <= len(nums) <= 10^4",
            "-10^9 <= nums[i] <= 10^9",
        ],
        test_cases=[
            TestCase(
                input_args={"nums": [2, 7, 11, 15], "target": 9},
                expected_output=[0, 1],
                description="Example 1: indices of 2 and 7"
            ),
            TestCase(
                input_args={"nums": [3, 2, 4], "target": 6},
                expected_output=[1, 2],
                description="Example 2: indices of 2 and 4"
            ),
            TestCase(
                input_args={"nums": [3, 3], "target": 6},
                expected_output=[0, 1],
                description="Edge case: same values"
            ),
        ],
        hints=[
            "What data structure lets you check if a number exists in O(1) time?",
            "Try using a hash map to store numbers you've seen and their indices.",
            "Iterate through nums once. For each num, check if (target - num) is in the map.",
        ],
        time_complexity="O(n)",
        space_complexity="O(n)",
    ),

    "valid_parentheses": ProblemSpec(
        id="valid_parentheses",
        title="Valid Parentheses",
        difficulty="easy",
        description="Given a string containing just the characters '(', ')', '{', '}', '[' and ']', determine if the input string is valid.",
        function_signature="def is_valid(s: str) -> bool:",
        constraints=[
            "1 <= len(s) <= 10^4",
            "s contains only '(', ')', '{', '}', '[', ']'",
        ],
        test_cases=[
            TestCase(
                input_args={"s": "()"},
                expected_output=True,
                description="Simple valid case"
            ),
            TestCase(
                input_args={"s": "()[]{}"},
                expected_output=True,
                description="Multiple bracket pairs"
            ),
            TestCase(
                input_args={"s": "(]"},
                expected_output=False,
                description="Mismatched brackets"
            ),
            TestCase(
                input_args={"s": "([)]"},
                expected_output=False,
                description="Interleaved brackets"
            ),
        ],
        hints=[
            "What data structure is LIFO (last in, first out)?",
            "Use a stack. Push opening brackets, pop when you see a closing bracket.",
            "A stack is a list. Use append() to push and pop() to remove. Check if top matches.",
        ],
        time_complexity="O(n)",
        space_complexity="O(n)",
    ),

    "merge_sorted_arrays": ProblemSpec(
        id="merge_sorted_arrays",
        title="Merge Sorted Arrays",
        difficulty="easy",
        description="Given two sorted integer arrays, merge them into a single sorted array.",
        function_signature="def merge(arr1: list[int], arr2: list[int]) -> list[int]:",
        constraints=[
            "0 <= len(arr1), len(arr2) <= 10^4",
            "Both arrays are already sorted in ascending order.",
        ],
        test_cases=[
            TestCase(
                input_args={"arr1": [1, 3, 5], "arr2": [2, 4, 6]},
                expected_output=[1, 2, 3, 4, 5, 6],
                description="Interleaved merge"
            ),
            TestCase(
                input_args={"arr1": [], "arr2": [1, 2]},
                expected_output=[1, 2],
                description="Empty first array"
            ),
            TestCase(
                input_args={"arr1": [1], "arr2": []},
                expected_output=[1],
                description="Empty second array"
            ),
        ],
        hints=[
            "You can iterate through both arrays and pick the smaller element.",
            "Use two pointers, one for each array. Compare and move the pointer of the smaller value.",
            "Merge by comparing arr1[i] and arr2[j], appending the smaller one, and advancing that pointer.",
        ],
        time_complexity="O(n + m)",
        space_complexity="O(n + m)",
    ),

    "reverse_linked_list": ProblemSpec(
        id="reverse_linked_list",
        title="Reverse Linked List",
        difficulty="medium",
        description="Reverse a singly linked list.",
        function_signature="def reverse_list(head: Optional[ListNode]) -> Optional[ListNode]:",
        constraints=[
            "The number of nodes in the list is in the range [0, 5000].",
            "-5000 <= Node.val <= 5000",
        ],
        test_cases=[
            TestCase(
                input_args={"head": "1->2->3->None"},
                expected_output="3->2->1->None",
                description="Example 1: reverse 3-node list"
            ),
            TestCase(
                input_args={"head": "None"},
                expected_output="None",
                description="Empty list"
            ),
            TestCase(
                input_args={"head": "1->None"},
                expected_output="1->None",
                description="Single node"
            ),
        ],
        hints=[
            "Keep track of the previous node. Iterate through the list and redirect each node's pointer.",
            "For each node, save the next pointer before modifying it. Then set node.next = prev and move forward.",
            "prev = None, curr = head. Loop: next_temp = curr.next, curr.next = prev, prev = curr, curr = next_temp.",
        ],
        time_complexity="O(n)",
        space_complexity="O(1)",
    ),

    "binary_search": ProblemSpec(
        id="binary_search",
        title="Binary Search",
        difficulty="medium",
        description="Given a sorted array of integers and a target value, return the index of the target if it is in the array, else return -1.",
        function_signature="def binary_search(nums: list[int], target: int) -> int:",
        constraints=[
            "1 <= len(nums) <= 10^4",
            "-10^9 <= nums[i] <= 10^9",
            "Array is sorted in ascending order.",
            "All integers in nums are unique.",
        ],
        test_cases=[
            TestCase(
                input_args={"nums": [-1, 0, 3, 5, 9, 12], "target": 9},
                expected_output=4,
                description="Target found at index 4"
            ),
            TestCase(
                input_args={"nums": [-1, 0, 3, 5, 9, 12], "target": 13},
                expected_output=-1,
                description="Target not in array"
            ),
        ],
        hints=[
            "Don't scan linearly. Use the fact that the array is sorted.",
            "Divide the search space in half each time. Set left and right pointers.",
            "Compare mid = (left + right) // 2 with target. Move left or right pointer based on comparison.",
        ],
        time_complexity="O(log n)",
        space_complexity="O(1)",
    ),

    "longest_substring_without_repeating": ProblemSpec(
        id="longest_substring_without_repeating",
        title="Longest Substring Without Repeating Characters",
        difficulty="medium",
        description="Given a string, find the length of the longest substring that does not contain repeating characters.",
        function_signature="def length_of_longest_substring(s: str) -> int:",
        constraints=[
            "0 <= len(s) <= 5 * 10^4",
            "s consists of English letters, digits, symbols and spaces.",
        ],
        test_cases=[
            TestCase(
                input_args={"s": "abcabcbb"},
                expected_output=3,
                description="Substring 'abc'"
            ),
            TestCase(
                input_args={"s": "bbbbb"},
                expected_output=1,
                description="Single character repeated"
            ),
            TestCase(
                input_args={"s": "pwwkew"},
                expected_output=3,
                description="Substring 'wke'"
            ),
        ],
        hints=[
            "Use a sliding window. Maintain a window of unique characters.",
            "Use a hash set or dict to track characters in the current window.",
            "Expand the window by moving right pointer. If char repeats, shrink from left until it's unique again.",
        ],
        time_complexity="O(n)",
        space_complexity="O(min(n, m))",  # m = charset size
    ),
}


def get_problem(problem_id: str) -> Optional[ProblemSpec]:
    """Get a problem by ID."""
    return PROBLEM_CATALOG.get(problem_id)


def list_problems(difficulty: Optional[str] = None) -> list[ProblemSpec]:
    """List all problems, optionally filtered by difficulty."""
    specs = list(PROBLEM_CATALOG.values())
    if difficulty:
        specs = [s for s in specs if s.difficulty == difficulty]
    return specs


def get_problem_for_role(role: str, seniority: str) -> Optional[str]:
    """Suggest a problem ID based on role and seniority (simple heuristic)."""
    if seniority == "junior":
        # Junior SDE: start with easy
        easy_problems = [p for p in PROBLEM_CATALOG.values() if p.difficulty == "easy"]
        return easy_problems[0].id if easy_problems else None
    elif seniority == "mid":
        # Mid-level: mix of easy and medium
        all_problems = [p for p in PROBLEM_CATALOG.values()]
        return all_problems[len(all_problems) // 2].id if all_problems else None
    else:  # senior
        # Senior: medium and hard
        medium_problems = [p for p in PROBLEM_CATALOG.values() if p.difficulty in ("medium", "hard")]
        return medium_problems[0].id if medium_problems else None


def get_hint_for_problem(problem_id: str, hint_level: int) -> Optional[str]:
    """Get a hint for a problem at the given level (1/2/3)."""
    spec = get_problem(problem_id)
    if not spec or not spec.hints:
        return None
    hint_idx = min(hint_level - 1, len(spec.hints) - 1)
    return spec.hints[hint_idx] if hint_idx >= 0 else None


__all__ = [
    "ProblemSpec",
    "TestCase",
    "PROBLEM_CATALOG",
    "get_problem",
    "list_problems",
    "get_problem_for_role",
    "get_hint_for_problem",
]
