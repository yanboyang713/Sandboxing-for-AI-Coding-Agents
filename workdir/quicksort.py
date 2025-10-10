"""
Pure-Python quicksort implementation.

This module provides quicksort(lst: list[int]) -> list[int], which returns a new
sorted list without mutating the input.

Doctest examples:
>>> quicksort([3, 1, 2])
[1, 2, 3]
>>> quicksort([])
[]
>>> quicksort([5, -1, 3, 3, 5])
[-1, 3, 3, 5, 5]
"""

def quicksort(lst: list[int]) -> list[int]:
    """Return a new list containing the elements of lst sorted using the quicksort algorithm.

    This implementation is non-destructive and uses a simple recursive strategy
    with a middle element as the pivot.

    Args:
        lst: A list of integers to sort.

    Returns:
        A new list with the elements of lst in non-decreasing order.

    Examples:
        >>> quicksort([3, 1, 2])
        [1, 2, 3]
        >>> quicksort([])
        []
        >>> quicksort([5, -1, 3, 3, 5])
        [-1, 3, 3, 5, 5]
    """
    if len(lst) <= 1:
        return lst[:]  # return a shallow copy to avoid aliasing
    pivot = lst[len(lst) // 2]
    left = [x for x in lst if x < pivot]
    middle = [x for x in lst if x == pivot]
    right = [x for x in lst if x > pivot]
    return quicksort(left) + middle + quicksort(right)


if __name__ == "__main__":
    import sys
    data = sys.stdin.read().strip()
    if data:
        try:
            nums = [int(token) for token in data.split()]
        except ValueError:
            print("Error: input must contain integers separated by whitespace.", file=sys.stderr)
            sys.exit(2)
        print(" ".join(str(n) for n in quicksort(nums)))