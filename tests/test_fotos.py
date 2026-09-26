"""La foto de la etiqueta, que es la prueba, guardada como hay que guardarla.

La etiqueta de un primal dice qué matadero, qué lote y qué fecha de sacrificio
traía la pieza. Se hace con el móvil, en la cámara, y hasta ahora se guardaba
tal cual llegaba: dos megas de los que lo único que se lee cabe en doscientos
cincuenta kilos, con el giro apuntado en una etiqueta que se pierde en cuanto
alguien toca el fichero, y con el GPS de quien la hizo dentro.

Lo que se mide aquí no es que pese menos: es que **se siga leyendo**. Una foto
de etiqueta que no se lee no es una prueba, es sitio ocupado.
"""
import io
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import service as meat
from thegrill.models import Primal, PrimalStatus
from thegrill.web import fotos, service as plataforma

from tests.meat_helpers import SPANISH, csrf_from, login, new_house


def _del_movil(ancho=4032, alto=3024, giro=None, gps=False, calidad=95) -> bytes:
    """Una foto como la que sale de un teléfono: una etiqueta y su grano.

    No vale un color plano —comprime a nada y no mide nada— ni ruido puro, que
    es lo contrario: no comprime nunca y haría parecer que esto no sirve. Una
    etiqueta de verdad es fondo claro, letra negra, y encima el grano del
    sensor de una cámara con poca luz.
    """
    import random

    from PIL import ImageDraw, ImageFilter

    imagen = Image.new("RGB", (ancho, alto), (238, 236, 230))
    dibujo = ImageDraw.Draw(imagen)
    alto_linea = max(4, alto // 240)
    for i in range(12):
        y = alto // 12 * i + alto // 30
        dibujo.rectangle([ancho // 12, y, ancho - ancho // 12, y + alto_linea],
                         fill=(18, 18, 20))
        dibujo.text((ancho // 12, y + alto_linea * 2),
                    f"LOTE 25-0917-4412-{chr(65 + i)}  ES 10.03456/M CE", fill=(18, 18, 20))
    # El grano, que es lo que de verdad pesa en una foto de cámara.
    grano = Image.new("L", (ancho // 8, alto // 8))
    azar = random.Random(7)
    grano.putdata([azar.randint(96, 160) for _ in range(grano.width * grano.height)])
    grano = grano.resize((ancho, alto), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1))
    imagen = Image.blend(imagen, Image.merge("RGB", (grano, grano, grano)), 0.12)

    exif = Image.Exif()
    if giro:
        exif[0x0112] = giro
    if gps:
        exif[34853] = {1: "N", 2: (40.0, 24.0, 51.0), 3: "O"}
    exif[0x010F] = "Marca del telefono"
    fuera = io.BytesIO()
    imagen.save(fuera, "JPEG", quality=calidad, exif=exif)
    return fuera.getvalue()


# ------------------------------------------------------ lo que hace al pasar
def test_a_phone_photo_comes_out_small_enough_to_keep_for_two_years():
    """Dos megas para leer una etiqueta térmica es pagar por grano de sensor."""
    crudo = _del_movil()
    salida, tipo = fotos.normaliza(crudo, "image/jpeg")
    assert tipo == "image/jpeg"
    assert len(salida) < len(crudo) / 3, (len(crudo), len(salida))
    assert max(Image.open(io.BytesIO(salida)).size) == fotos.LADO_MAX


def test_the_turn_goes_into_the_pixels_and_not_into_a_note():
    """Un móvil en vertical guarda los píxeles tumbados y lo apunta aparte.

    Si esa nota se pierde —y se pierde en cuanto alguien toca el fichero— la
    prueba queda de lado para siempre.
    """
    salida, _ = fotos.normaliza(_del_movil(giro=6), "image/jpeg")
    guardada = Image.open(io.BytesIO(salida))
    assert guardada.height > guardada.width, "se guardó tumbada"
    assert not guardada.getexif().get(0x0112), "quedó la nota en vez del giro"


def test_where_that_person_was_standing_does_not_get_kept_for_two_years():
    """El GPS del EXIF es un dato personal de un empleado, no de la carne."""
    salida, _ = fotos.normaliza(_del_movil(gps=True), "image/jpeg")
    assert not Image.open(io.BytesIO(salida)).getexif()


def test_something_that_is_not_a_photo_never_reaches_the_disk():
    """Solo se miraba el tipo que decía quien lo subía, y la extensión salía de ahí."""
    with pytest.raises(fotos.FotoIlegible):
        fotos.normaliza(b"MZ\x90\x00esto es un programa", "image/jpeg")


def test_a_pdf_attached_to_a_sheet_is_left_alone():
    """Aquí solo se arreglan imágenes: lo demás se guarda como viene."""
    crudo = b"%PDF-1.4 lo que sea"
    assert fotos.normaliza(crudo, "application/pdf") == (crudo, "application/pdf")


def test_a_photo_that_was_already_small_is_never_blown_up():
    """Lo que ya era pequeño no se agranda, y no sale pesando más que como entró."""
    pequeña = _del_movil(ancho=800, alto=600, calidad=60)
    salida, _ = fotos.normaliza(pequeña, "image/jpeg")
    assert Image.open(io.BytesIO(salida)).size == (800, 600)
    assert len(salida) <= len(pequeña)


def test_a_png_that_unfolds_into_a_thousand_million_pixels_is_refused():
    """Un fichero pequeño hecho a propósito para dejar al servidor sin memoria."""
    assert fotos.TOPE_PIXELES <= 100_000_000


# ------------------------------------------------ y eso, subiendo de verdad
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    monkeypatch.setattr(meatapp, "UPLOAD_DIR", str(tmp_path / "subidas"))
    db.init_engine(f"sqlite:///{tmp_path/'fotos.db'}")
    db.create_all()
    new_house(language="es")
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        login(c)
        yield c


def _una_pieza():
    with db.session_scope() as s:
        from thegrill.models import User
        quien = s.query(User).filter_by(email="albano@marina.com").one()
        s.add(Primal(restaurant_id=quien.restaurant_id, serial="8017", sku="RIBEYE",
                     weight_kg=9.0, received_kg=9.0, landed_usd_per_kg=30.0,
                     piece_cost_usd=270.0, status=PrimalStatus.IN_STOCK))


def test_the_label_photo_of_a_piece_is_shrunk_on_the_way_in(client):
    """La misma regla, por la puerta por la que entra de verdad."""
    _una_pieza()
    crudo = _del_movil(giro=6, gps=True)
    token = csrf_from(client.get("/recepcion").text)
    r = client.post("/carne/8017/foto", data={"csrf": token},
                    files={"foto": ("etiqueta.jpg", crudo, "image/jpeg")})
    assert r.status_code in (200, 303), r.text[:300]
    with db.session_scope() as s:
        guardada = s.query(Primal).filter_by(serial="8017").one().photo_ref
    assert guardada and os.path.exists(guardada)
    assert os.path.getsize(guardada) < len(crudo) / 3
    imagen = Image.open(guardada)
    assert max(imagen.size) == fotos.LADO_MAX
    assert imagen.height > imagen.width          # derecha
    assert not imagen.getexif()                  # y sin GPS


def test_a_photo_that_no_one_can_open_is_refused_at_the_door(client):
    """HEIC se aceptaba y luego no lo abría ni el servidor ni el inspector."""
    assert "image/heic" not in plataforma.ALLOWED_IMAGE_TYPES
    _una_pieza()
    token = csrf_from(client.get("/recepcion").text)
    r = client.post("/carne/8017/foto", data={"csrf": token},
                    files={"foto": ("etiqueta.heic", b"\x00\x00\x00 ftypheic", "image/heic")})
    # Se vuelve a la pantalla con el aviso puesto, no en silencio.
    assert "foto=" in r.headers.get("location", ""), r.headers
    with db.session_scope() as s:
        assert s.query(Primal).filter_by(serial="8017").one().photo_ref is None


def test_the_label_is_still_readable_after_being_shrunk():
    """Lo que importa no es que pese menos: es que se siga leyendo.

    Se dibuja una etiqueta con el lote y el óvalo sanitario, se fotografía en
    grande y se pasa por el mismo camino que una foto de verdad. Después se
    compara letra a letra con el original reducido al mismo tamaño: si la
    compresión se hubiera comido los bordes de las cifras, la diferencia
    saltaría aquí.
    """
    from PIL import ImageChops, ImageDraw

    etiqueta = Image.new("RGB", (3000, 2000), (245, 245, 240))
    dibujo = ImageDraw.Draw(etiqueta)
    for i, linea in enumerate(["LOTE 25-0917-4412-A", "ES 10.03456/M CE",
                               "SACRIFICIO 12/09/2025", "PESO 24,380 kg"]):
        dibujo.text((120, 200 + i * 260), linea, fill=(10, 10, 10))
        dibujo.rectangle([120, 320 + i * 260, 2880, 326 + i * 260], fill=(10, 10, 10))
    crudo = io.BytesIO()
    etiqueta.save(crudo, "JPEG", quality=96)

    salida, _ = fotos.normaliza(crudo.getvalue(), "image/jpeg")
    guardada = Image.open(io.BytesIO(salida)).convert("L")
    patron = etiqueta.resize(guardada.size, Image.LANCZOS).convert("L")

    # Cuántos píxeles se han movido de verdad respecto al patrón. En las líneas
    # negras sobre fondo claro de una etiqueta, un cambio real de trazo se ve.
    diferencia = ImageChops.difference(guardada, patron)
    gordos = sum(n for valor, n in enumerate(diferencia.histogram()) if valor > 60)
    assert gordos / (guardada.width * guardada.height) < 0.01, gordos
