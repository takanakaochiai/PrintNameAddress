import ctypes
import csv
import io
import json
import os
import re
from contextlib import closing
from math import ceil
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image, ImageDraw, ImageFont
from pypdfium2 import PdfDocument, raw


FORMATS = {
    "": "そのまま",
    "postal3": "郵便番号（前3桁）",
    "postal4": "郵便番号（後4桁）",
    "era": "生年月日（元号）",
    "year": "生年月日（年）",
    "month": "生年月日（月）",
    "day": "生年月日（日）",
}


def load_csv(path):
    data = path.read_bytes()
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = data.decode("cp932")
    reader = csv.reader(io.StringIO(content, newline=""), strict=True)
    columns = [value.strip() for value in next(reader, [])]
    if not columns or not all(columns) or len(set(columns)) != len(columns):
        raise ValueError("CSVの1行目には重複のない列名を指定してください。")
    rows = []
    for row in reader:
        if not row or not any(row):
            continue
        if len(row) != len(columns):
            raise ValueError(f"CSVの{reader.line_num}行目の列数が見出しと一致しません。")
        rows.append(dict(zip(columns, row)))
    if not rows:
        raise ValueError("CSVにデータ行がありません。")
    return columns, rows


def load_rules(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("対応表はJSONオブジェクトで指定してください。")
    rules = {}
    for name, rule in data.items():
        if isinstance(rule, str):
            rule = {"column": rule}
        if (not isinstance(rule, dict) or not isinstance(rule.get("column"), str)
                or rule.get("format", "") not in FORMATS
                or type(rule.get("cells", 0)) is not int
                or not 0 <= rule.get("cells", 0) <= 100):
            raise ValueError(f"対応表の「{name}」の設定を確認してください。")
        rules[name] = rule
    return rules


def save_rules(path, rules):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                            suffix=".tmp", delete=False) as output:
        pending = Path(output.name)
    try:
        pending.write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def format_value(value, fmt):
    if not fmt or not value.strip():
        return value
    if fmt.startswith("postal"):
        digits = re.sub(r"[\s\-－−〒]", "", value)
        if re.fullmatch(r"[0-9]{7}", digits):
            return digits[:3] if fmt == "postal3" else digits[3:]
        return ""
    match = re.fullmatch(r"\s*(明治|大正|昭和|平成|令和)?\s*(元|\d{1,4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*", value)
    if not match:
        match = re.fullmatch(r"\s*()(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*", value)
    if match:
        return match.group(("era", "year", "month", "day").index(fmt) + 1) or ""
    return ""


def field_name(form, annot):
    size = raw.FPDFAnnot_GetFormFieldName(form, annot, None, 0)
    if size <= 2:
        return ""
    buffer = (ctypes.c_ushort * (size // 2))()
    raw.FPDFAnnot_GetFormFieldName(form, annot, buffer, size)
    return bytes(buffer).decode("utf-16-le").rstrip("\0")


def widgets(page, form):
    for index in range(raw.FPDFPage_GetAnnotCount(page)):
        annot = raw.FPDFPage_GetAnnot(page, index)
        if not annot:
            continue
        try:
            if raw.FPDFAnnot_GetSubtype(annot) != raw.FPDF_ANNOT_WIDGET:
                continue
            field_type = raw.FPDFAnnot_GetFormFieldType(form, annot)
            if field_type in (raw.FPDF_FORMFIELD_TEXTFIELD, raw.FPDF_FORMFIELD_COMBOBOX):
                name = field_name(form, annot)
                if name:
                    yield index, name, annot
        finally:
            raw.FPDFPage_CloseAnnot(annot)


def widget_box(page, annot, size):
    rect = raw.FS_RECTF()
    if not raw.FPDFAnnot_GetRect(annot, rect):
        return None
    points = []
    for px, py in ((rect.left, rect.bottom), (rect.right, rect.top)):
        x, y = ctypes.c_long(), ctypes.c_long()
        if not raw.FPDF_PageToDevice(page, 0, 0, *size, 0, px, py, x, y):
            return None
        points.append((x.value, y.value))
    (x1, y1), (x2, y2) = points
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def draw_value(image, box, text, rotation, scale, cells, centered):
    x1, y1, x2, y2 = box
    width, height = x2 - x1, y2 - y1
    if rotation in (90, 270):
        width, height = height, width
    if width <= 1 or height <= 1:
        return
    tile = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(tile)
    lines = text.splitlines() or [""]
    padding = min(width // 6, max(1, round(scale * 1.5)))
    font_path = Path(os.environ["WINDIR"]) / "Fonts" / "msgothic.ttc"
    for size in range(max(2, min(round(12 * scale), height - 1)), 1, -1):
        font = ImageFont.truetype(str(font_path), size)
        left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font, spacing=1)
        if cells and len(text) <= cells:
            fits_width = max(map(font.getlength, text)) <= width / cells
        else:
            fits_width = right - left <= width - 2 * padding
        if fits_width and bottom - top <= height:
            break
    if cells and len(text) <= cells and len(lines) == 1:
        for index, character in enumerate(text):
            draw.text(((index + 0.5) * width / cells, (height - bottom - top) / 2),
                      character, font=font, fill="black", anchor="ma")
    else:
        x = (width - right - left) / 2 if centered else padding - left
        draw.multiline_text((x, (height - bottom - top) / 2), text,
                            font=font, fill="black", spacing=1)
    if rotation:
        tile = tile.rotate(-rotation, expand=True)
    image.paste(tile, (x1, y1), tile)


class PdfForm:
    def __init__(self, path):
        self.doc = PdfDocument(path)
        try:
            self.doc.init_forms()
            self.fields = {}
            for page_index in range(len(self.doc)):
                with closing(self.doc[page_index]) as page:
                    for _, name, _ in widgets(page, self.doc.formenv):
                        self.fields.setdefault(name, page_index)
        except Exception:
            self.close()
            raise

    def close(self):
        self.doc.close()

    def render(self, page_index, scale, rules, row, highlight=None, printing=False):
        with closing(self.doc[page_index]) as page:
            base_width, base_height = page.get_size()
            size = ceil(base_width * scale), ceil(base_height * scale)
            overlays = []
            selection = []
            hidden = {}
            try:
                for index, name, annot in widgets(page, self.doc.formenv):
                    rule = rules.get(name)
                    if name != highlight and (not rule or rule["column"] not in row):
                        continue
                    box = widget_box(page, annot, size)
                    if box is None:
                        continue
                    flags = raw.FPDFAnnot_GetFlags(annot)
                    if name == highlight:
                        selection.append(box)
                    if rule and rule["column"] in row and not flags & 3:
                        if raw.FPDFAnnot_SetFlags(annot, flags | 2):
                            hidden[index] = flags
                        value = format_value(row[rule["column"]], rule.get("format", ""))
                        if value.strip() and (not printing or flags & 4):
                            overlays.append((box, value, rule.get("cells", 0),
                                             bool(rule.get("center", False))))
                with closing(page.render(scale=scale, optimize_mode="print")) as bitmap:
                    image = bitmap.to_pil().convert("RGB")
            finally:
                for index, _, annot in widgets(page, self.doc.formenv):
                    if index in hidden:
                        raw.FPDFAnnot_SetFlags(annot, hidden[index])
            for box, value, cells, centered in overlays:
                draw_value(image, box, value, page.get_rotation(), scale, cells, centered)
            for box in selection:
                ImageDraw.Draw(image).rectangle(box, outline="#0875e1", width=3)
            return image, (base_width, base_height)
