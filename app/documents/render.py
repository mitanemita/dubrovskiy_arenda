"""Рендеринг HTML-шаблонов (Jinja2) и конвертация в PDF (WeasyPrint)."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _money_fmt(value) -> str:
    """Формат суммы: 51 680,00 (пробел-разделитель тысяч, запятая-копейки)."""
    if value is None:
        return ""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    s = f"{d:,.2f}"  # 51,680.00
    return s.replace(",", " ").replace(".", ",")


_env.filters["money"] = _money_fmt


def render_html(template_name: str, context: dict) -> str:
    """Рендерит HTML-шаблон с контекстом."""
    return _env.get_template(template_name).render(**context)


def html_to_pdf(html: str) -> bytes:
    """Конвертирует HTML в PDF. Импорт WeasyPrint ленивый (тяжёлые нативные зависимости)."""
    from weasyprint import HTML  # noqa: PLC0415

    return HTML(string=html).write_pdf()


def render_pdf(template_name: str, context: dict) -> bytes:
    """HTML-шаблон -> PDF-байты."""
    return html_to_pdf(render_html(template_name, context))
