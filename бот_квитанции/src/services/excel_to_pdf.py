import subprocess
from pathlib import Path
import fitz
from PIL import Image
import traceback
BASE_DIR = Path(__file__).resolve().parent.parent.parent


TEMPLATE_CONFIG = {
    "Акт_аренда": {
        "margin_bottom": 350,
        "margin_left": 40,
        "image_width": 150
    },
    "Акт_ком_услуги1": {
        "margin_bottom": 200,
        "margin_left": 150,
        "image_width": 300
    },
    "Акт_ком_услуги2": {
        "margin_bottom": 150,
        "margin_left": 150,
        "image_width": 300
    },
    "Акт_ком_услуги3": {
        "margin_bottom": 100,
        "margin_left": 150,
        "image_width": 300
    },
    "Акт_ком_услуги4": {
        "margin_bottom": 50,
        "margin_left": 150,
        "image_width": 300
    },
    "Акт_ком_услуги5": {
        "margin_bottom": 0,
        "margin_left": 150,
        "image_width": 300
    },
    "Счет_за_аренду": {
        "margin_bottom": 0,
        "margin_left": 0,
        "image_width": 600
    },
    "Счет_за_просрочку": {
        "margin_bottom": 0,
        "margin_left": 0,
        "image_width": 600
    },
    "Счет_за_электричество": {
        "margin_bottom": 0,
        "margin_left": 0,
        "image_width": 600
    },
    "default": {
        "margin_bottom": 100,
        "margin_left": 50,
        "image_width": 200
    }
}

def get_template_params(filename):
    """Определяет параметры для вставки изображения на основе названия файла"""
    filename_lower = filename.lower()
    for template in TEMPLATE_CONFIG:
        if template.lower() in filename_lower and template != "default":
            print(f"Найден шаблон: {template}")
            return TEMPLATE_CONFIG[template]
    print("Используются параметры по умолчанию")
    return TEMPLATE_CONFIG["default"]

def convert_excel_to_pdf(xlsx_path, pdf_path, user_id):
    try:
        xlsx_abs = (BASE_DIR / xlsx_path).resolve()
        pdf_abs = (BASE_DIR / pdf_path).resolve()
        output_dir = pdf_abs.parent

        soffice_path = r"C:\Program Files\LibreOffice\program\soffice.exe"
        result = subprocess.run([
            soffice_path,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", str(output_dir),
            str(xlsx_abs)
        ], capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"Ошибка конвертации: {result.stderr}")

        if not pdf_abs.exists():
            raise FileNotFoundError(f"PDF не создан: {pdf_abs}")

        template_params = get_template_params(xlsx_abs.name)
        print(f"Параметры для вставки: {template_params}")

        image_path = BASE_DIR / "data" / "photos" / f"{user_id}.png"
        if not image_path.exists():
            raise FileNotFoundError(f"Фото не найдено: {image_path}")

        insert_image_into_pdf(
            pdf_path=pdf_abs,
            image_path=image_path,
            **template_params
        )

        print(f"Успешно создан PDF: {pdf_abs}")

    except Exception as e:
        traceback.print_exc()
        raise

def insert_image_into_pdf(pdf_path, image_path, margin_bottom=50, margin_left=50, image_width=200):
    """Вставляет изображение с указанными параметрами"""
    try:
        with Image.open(image_path) as img:
            img_width, img_height = img.size
            aspect_ratio = img_height / img_width

        doc = fitz.open(pdf_path)
        page = doc[0]
        page_rect = page.rect

        new_height = image_width * aspect_ratio
        x_position = margin_left
        y_position = page_rect.height - margin_bottom - new_height

        if x_position + image_width > page_rect.width:
            image_width = page_rect.width - margin_left
            new_height = image_width * aspect_ratio

        if y_position < 0:
            new_height = page_rect.height - margin_bottom
            image_width = new_height / aspect_ratio

        rect = fitz.Rect(
            x_position,
            y_position,
            x_position + image_width,
            y_position + new_height
        )
        
        page.insert_image(rect, filename=str(image_path), keep_proportion=True)
        doc.saveIncr()
        print(f"Изображение вставлено: X={x_position}, Y={y_position}, Размер={image_width}x{new_height}")

    except Exception as e:
        traceback.print_exc()
        raise

if __name__ == "__main__":
    try:
        convert_excel_to_pdf(
            xlsx_path=Path("data/files/5Счет_на_оплату_№_5К03_25_от_03_апреля_25 (2).xlsx"),
            pdf_path=Path("data/files/5Счет_на_оплату_№_5К03_25_от_03_апреля_25 (2).pdf"),
            user_id="1374296012"
        )
    except Exception as e:
        print(f"\nОшибка выполнения: {str(e)}")