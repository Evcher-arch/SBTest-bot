"""Объединение результатов векторного и полнотекстового поиска (RRF)."""

from __future__ import annotations

import pytest

from services.search.retrieval import RRF_K, rrf


def test_identical_lists_keep_order():
    fused = rrf([[1, 2, 3], [1, 2, 3]])
    assert [d for d, _ in fused] == [1, 2, 3]
    assert fused[0][1] == pytest.approx(2 / (RRF_K + 1))


def test_document_in_both_lists_wins():
    fused = rrf([[1, 2, 3], [4, 3, 5]])
    assert fused[0][0] == 3  # 1/(60+3) + 1/(60+2) больше, чем 1/(60+1)


def test_disjoint_lists_interleave_by_rank():
    fused = rrf([[1, 2], [3, 4]])
    ids = [d for d, _ in fused]
    assert set(ids[:2]) == {1, 3}
    assert set(ids[2:]) == {2, 4}


def test_top_k_limit():
    fused = rrf([[1, 2, 3, 4, 5], [6, 7, 8]], top_k=3)
    assert len(fused) == 3


def test_empty_inputs():
    assert rrf([[], []]) == []
    assert rrf([[7], []]) == [(7, pytest.approx(1 / (RRF_K + 1)))]
