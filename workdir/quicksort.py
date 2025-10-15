def quicksort(lst: list[int]) -> list[int]:
    """
    Return a new list containing the elements of lst sorted using the quicksort algorithm.

    This implementation is pure Python and does not modify the input list.

    Doctests:
    >>> quicksort([3, 1, 2])
    [1, 2, 3]
    >>> quicksort([])
    []
    >>> quicksort([5, -1, 3, 5, 2])
    [-1, 2, 3, 5, 5]
    """
    if len(lst) <= 1:
        return lst[:]
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
            nums = [int(tok) for tok in data.split()]
        except ValueError:
            print("Error: all inputs must be integers.", file=sys.stderr)
            sys.exit(1)
    else:
        nums = []

    sorted_nums = quicksort(nums)
    print(" ".join(map(str, sorted_nums)))