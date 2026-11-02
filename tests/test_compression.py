import tarfile
import zipfile

import pytest
from PIL import Image

from fileotter.compression import CompressionError, compress, compress_image, compress_pdf, compress_xlsx, extract


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "docs"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("hello " * 1000)
    (d / "sub" / "b.txt").write_text("world " * 1000)
    return d


@pytest.mark.parametrize("fmt", ["zip", "tar.gz", "tar.bz2", "tar.xz"])
def test_compress_and_extract_roundtrip(folder, tmp_path, fmt):
    archive = compress([folder], fmt)
    assert archive.name == f"docs.{fmt}"
    out = extract(archive, tmp_path / "out")
    assert (out / "docs" / "sub" / "b.txt").read_text() == "world " * 1000


def test_gz_single_file(folder, tmp_path):
    gz = compress([folder / "a.txt"], "gz")
    assert gz.stat().st_size < (folder / "a.txt").stat().st_size
    assert extract(gz, tmp_path / "a.txt").read_text() == "hello " * 1000
    with pytest.raises(CompressionError, match="single file"):
        compress([folder], "gz", tmp_path / "x.gz")


def test_extract_rejects_zip_slip(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escaped.txt", "nope")
    with pytest.raises(CompressionError, match="Unsafe path"):
        extract(evil, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_extract_rejects_tar_traversal(tmp_path):
    payload = tmp_path / "p.txt"
    payload.write_text("nope")
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        tf.add(payload, arcname="../../escaped.txt")
    with pytest.raises(CompressionError, match="Unsafe path"):
        extract(evil, tmp_path / "out" / "deep")


def test_compress_image_shrinks_jpeg(tmp_path):
    src = tmp_path / "photo.jpg"
    Image.effect_noise((800, 800), 64).convert("RGB").save(src, quality=95)
    dst = compress_image(src, quality=40, max_dimension=400)
    assert dst.name == "photo.compressed.jpg"
    assert dst.stat().st_size < src.stat().st_size
    with Image.open(dst) as img:
        assert max(img.size) == 400


def _noisy_photo(size=(1600, 1600)):
    return Image.effect_noise(size, 40).convert("RGB")


def test_compress_pdf_shrinks_images_and_keeps_pages(tmp_path):
    from pypdf import PdfReader

    src = tmp_path / "scan.pdf"
    _noisy_photo().save(src, save_all=True, append_images=[_noisy_photo()], quality=95)
    dst = compress_pdf(src, quality=40, max_dimension=800)

    assert dst.name == "scan.compressed.pdf"
    assert dst.stat().st_size < src.stat().st_size * 0.5
    reader = PdfReader(dst)
    assert len(reader.pages) == 2
    assert max(reader.pages[0].images[0].image.size) == 800


def test_compress_pdf_keeps_text(tmp_path):
    from pypdf import PdfReader, PdfWriter

    src = tmp_path / "text.pdf"
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.write(src)
    dst = compress_pdf(src)
    assert len(PdfReader(dst).pages) == 1


def test_compress_pdf_guards(tmp_path):
    from pypdf import PdfWriter

    locked = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("secret")
    writer.write(locked)
    with pytest.raises(CompressionError, match="password"):
        compress_pdf(locked)
    not_pdf = tmp_path / "x.txt"
    not_pdf.write_text("x")
    with pytest.raises(CompressionError, match="Expected a .pdf"):
        compress_pdf(not_pdf)
    with pytest.raises(CompressionError, match="quality"):
        compress_pdf(locked, quality=0)


def test_compress_xlsx_preserves_data_and_shrinks_images(tmp_path):
    from openpyxl import Workbook, load_workbook
    from openpyxl.drawing.image import Image as XLImage

    photo = tmp_path / "photo.jpg"
    _noisy_photo().save(photo, quality=95)
    src = tmp_path / "report.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["item", "qty", "total"])
    ws.append(["widget", 3, "=B2*2"])
    ws.add_image(XLImage(photo), "E2")
    wb.save(src)

    dst = compress_xlsx(src, quality=40, max_dimension=800)
    assert dst.name == "report.compressed.xlsx"
    assert dst.stat().st_size < src.stat().st_size * 0.6

    ws2 = load_workbook(dst).active
    assert [c.value for c in ws2[2]] == ["widget", 3, "=B2*2"]  # formulas kept verbatim
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
        assert a.namelist() == b.namelist()  # every part survives, in order
        assert a.read("xl/worksheets/sheet1.xml") == b.read("xl/worksheets/sheet1.xml")


def test_compress_xlsx_rejects_non_workbooks(tmp_path):
    fake = tmp_path / "fake.xlsx"
    fake.write_text("not a zip")
    with pytest.raises(CompressionError, match="not a valid Excel"):
        compress_xlsx(fake)
