"""Денежные хелперы: округление и сумма прописью на русском (без внешних зависимостей)."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    """Приводит значение к денежному Decimal с округлением до копеек (полукруг вверх)."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


# --- Сумма прописью ---------------------------------------------------------
_UNITS_M = ("ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_UNITS_F = ("ноль", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_TEENS = (
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
    "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
)
_TENS = (
    "", "", "двадцать", "тридцать", "сорок", "пятьдесят",
    "шестьдесят", "семьдесят", "восемьдесят", "девяносто",
)
_HUNDREDS = (
    "", "сто", "двести", "триста", "четыреста", "пятьсот",
    "шестьсот", "семьсот", "восемьсот", "девятьсот",
)

# Разряды: (ед., 2-4, 5+), женский род для тысяч
_SCALES = (
    (("", "", ""), False),                              # единицы (род задаётся снаружи)
    (("тысяча", "тысячи", "тысяч"), True),
    (("миллион", "миллиона", "миллионов"), False),
    (("миллиард", "миллиарда", "миллиардов"), False),
    (("триллион", "триллиона", "триллионов"), False),
)


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    """Выбор формы слова по числу: (1, 2-4, 5-20/0)."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    d = n % 10
    if d == 1:
        return forms[0]
    if 2 <= d <= 4:
        return forms[1]
    return forms[2]


def _triple_to_words(num: int, feminine: bool) -> list[str]:
    """Слова для числа 0..999."""
    words: list[str] = []
    hundreds, rem = divmod(num, 100)
    if hundreds:
        words.append(_HUNDREDS[hundreds])
    if 10 <= rem <= 19:
        words.append(_TEENS[rem - 10])
    else:
        tens, units = divmod(rem, 10)
        if tens:
            words.append(_TENS[tens])
        if units:
            words.append((_UNITS_F if feminine else _UNITS_M)[units])
    return words


def int_to_words_ru(number: int, *, feminine: bool = False) -> str:
    """Целое число прописью. `feminine` — женский род единиц (для 'одна'/'две')."""
    if number == 0:
        return "ноль"
    if number < 0:
        return "минус " + int_to_words_ru(-number, feminine=feminine)

    # Разбиваем на группы по 3 разряда, младшая группа первая
    groups: list[int] = []
    n = number
    while n > 0:
        n, rem = divmod(n, 1000)
        groups.append(rem)

    parts: list[str] = []
    for idx in range(len(groups) - 1, -1, -1):
        group = groups[idx]
        if group == 0:
            continue
        scale_forms, scale_fem = _SCALES[idx]
        is_fem = scale_fem or (idx == 0 and feminine)
        parts.extend(_triple_to_words(group, is_fem))
        if idx > 0:
            parts.append(_plural(group, scale_forms))
    return " ".join(parts)


def rubles_kopecks_in_words(amount: Decimal | int | float | str) -> str:
    """Сумма прописью: 'Пятьдесят одна тысяча шестьсот восемьдесят рублей 00 копеек'."""
    amount = money(amount)
    sign = "минус " if amount < 0 else ""
    amount = abs(amount)
    rub = int(amount)
    kop = int((amount - rub) * 100)

    rub_words = int_to_words_ru(rub, feminine=False)
    rub_unit = _plural(rub, ("рубль", "рубля", "рублей"))
    kop_unit = _plural(kop, ("копейка", "копейки", "копеек"))

    text = f"{sign}{rub_words} {rub_unit} {kop:02d} {kop_unit}"
    return text[0].upper() + text[1:]
