"""The explicit transaction kind.

The decision is recorded in docs/adr/0010. The point worth restating here: a
sign carries one bit, so classifying by `amount > 0` supports two kinds and no
more. `kind` is stored, and the sign of `amount` says only which direction the
money moved.
"""
import pytest

from gigledger.models import (COST_KINDS, EXPENSE, INCOME, INVENTORY, KINDS,
                              RecurringTransaction, Transaction, clean_kind)


def test_the_vocabulary_is_three_kinds():
    assert KINDS == {'income', 'expense', 'inventory'}
    assert (INCOME, EXPENSE, INVENTORY) == ('income', 'expense', 'inventory')


def test_only_expenses_reduce_profit():
    """Inventory is cash out but not a cost: the money bought an asset."""
    assert COST_KINDS == {EXPENSE}
    assert INVENTORY not in COST_KINDS
    assert INCOME not in COST_KINDS


@pytest.mark.parametrize('value', ['income', 'expense', 'inventory'])
def test_clean_kind_accepts_every_known_kind(value):
    assert clean_kind(value) == value


@pytest.mark.parametrize('value', ['INCOME', ' Expense ', 'Inventory'])
def test_clean_kind_normalises_case_and_whitespace(value):
    assert clean_kind(value) in KINDS


@pytest.mark.parametrize('value', ['', None, 'asset', 'drop table', 'Income '])
def test_clean_kind_falls_back_for_anything_unrecognised(value):
    """A Constrained Column: the write decides, so the column cannot hold a
    kind the rest of the code has never heard of."""
    assert clean_kind(value, fallback=EXPENSE) in KINDS


def test_clean_kind_honours_an_explicit_fallback():
    assert clean_kind('nonsense', fallback=INCOME) == INCOME


@pytest.mark.parametrize('model', [Transaction, RecurringTransaction])
def test_both_models_classify_the_same_way(model):
    assert model(kind=INCOME).is_income
    assert model(kind=EXPENSE).is_expense
    assert model(kind=INVENTORY).is_inventory
    assert not model(kind=INCOME).is_expense
    assert not model(kind=EXPENSE).is_inventory


@pytest.mark.parametrize('kind,label', [
    (INCOME, 'Income'), (EXPENSE, 'Expense'), (INVENTORY, 'Inventory')])
def test_kind_label_is_display_ready(kind, label):
    assert Transaction(kind=kind).kind_label == label


def test_kind_does_not_follow_the_sign_of_the_amount():
    """The whole point: an amount's sign no longer decides what it is."""
    assert Transaction(kind=INVENTORY, amount=-980.00).is_inventory
    assert not Transaction(kind=INVENTORY, amount=-980.00).is_expense
