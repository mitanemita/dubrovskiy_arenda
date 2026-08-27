"""Тесты денежных хелперов и суммы прописью."""
from decimal import Decimal

import pytest

from app.domain.money import int_to_words_ru, money, rubles_kopecks_in_words


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "ноль"),
        (1, "один"),
        (11, "одиннадцать"),
        (21, "двадцать один"),
        (100, "сто"),
        (123, "сто двадцать три"),
        (1000, "одна тысяча"),
        (2000, "две тысячи"),
        (5000, "пять тысяч"),
        (51680, "пятьдесят одна тысяча шестьсот восемьдесят"),
        (1000000, "один миллион"),
    ],
)
def test_int_to_words(value, expected):
    assert int_to_words_ru(value) == expected


def test_thousands_feminine():
    # 21 000 -> "двадцать одна тысяча" (женский род для тысяч)
    assert int_to_words_ru(21000) == "двадцать одна тысяча"
    assert int_to_words_ru(22000) == "двадцать две тысячи"


@pytest.mark.parametrize(
    "amount,expected",
    [
        (Decimal("50000.00"), "Пятьдесят тысяч рублей 00 копеек"),
        (Decimal("51680.00"), "Пятьдесят одна тысяча шестьсот восемьдесят рублей 00 копеек"),
        (Decimal("1.01"), "Один рубль 01 копейка"),
        (Decimal("2.02"), "Два рубля 02 копейки"),
        (Decimal("5.05"), "Пять рублей 05 копеек"),
        (Decimal("1808.80"), "Одна тысяча восемьсот восемь рублей 80 копеек"),
    ],
)
def test_rubles_in_words(amount, expected):
    assert rubles_kopecks_in_words(amount) == expected


def test_money_rounding():
    assert money(Decimal("10.005")) == Decimal("10.01")  # полукруг вверх
    assert money("1680") == Decimal("1680.00")
    assert money(120 * 14) == Decimal("1680.00")
