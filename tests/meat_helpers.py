"""Cómo se crea una cuenta en las pruebas de la edición de carne.

En esta edición nadie se registra solo: la plataforma da de alta la casa y su
manager, y el manager da de alta a su gente. Estas funciones hacen eso mismo,
sin pasar por pantallas que ya no existen.
"""
import re

from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import billing
from thegrill.models import Billing, Restaurant, Role, User

# El navegador de estas pruebas habla español: los textos que se comprueban
# abajo son los españoles. Quien llega sin decir nada recibe inglés.
SPANISH = {"accept-language": "es"}


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "la página no trae token CSRF"
    return match.group(1)


def new_house(name="Hotel Marina", email="albano@marina.com", manager="Albano",
              password="clave-larga-1", language="es") -> int:
    """Da de alta una casa con su manager, como hace la plataforma."""
    with db.session_scope() as session:
        if not billing.owner_exists(session):
            billing.bootstrap_owner(session, "dueno@plataforma.com", "Dueño",
                                    "clave-plataforma-1")
        restaurant, _ = billing.create_account(
            session, name=name, manager_name=manager, manager_email=email,
            password=password, language=language)
        # En las pruebas la casa está al día: lo que se mide es la cocina.
        restaurant.billing = Billing.ACTIVE
        return restaurant.id


def login(client: TestClient, email="albano@marina.com", password="clave-larga-1"):
    response = client.post("/login", data={"email": email, "password": password})
    assert response.status_code == 303, response.text[:300]
    return response


def signup(client: TestClient, restaurant="Hotel Marina", email="albano@marina.com",
           name="Albano", language="es", password="clave-larga-1"):
    """Casa nueva y su manager dentro, listo para trabajar."""
    new_house(restaurant, email, name, password, language or "es")
    return login(client, email, password)


def add_user(client_or_email, email="marta@marina.com", name="Marta",
             password="clave-larga-2", role=Role.EMPLOYEE, house=None) -> TestClient:
    """Crea una cuenta de la casa y devuelve una sesión suya ya dentro."""
    with db.session_scope() as session:
        if house is None:
            restaurant = (session.query(Restaurant)
                          .filter(Restaurant.platform.isnot(True)).first())
        else:
            restaurant = session.get(Restaurant, house)
        manager = (session.query(User)
                   .filter_by(restaurant_id=restaurant.id, role=Role.MANAGER).first())
        billing.create_user(session, manager, name=name, email=email,
                            password=password, role=role if role != Role.MANAGER else Role.BUTCHER)
        if role == Role.MANAGER:
            session.query(User).filter_by(email=email).one().role = Role.MANAGER

    session_client = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    login(session_client, email, password)
    return session_client


def join_code(slug=None) -> str:
    """Compatibilidad: ya no se entra con código, pero alguna prueba lo pide."""
    with db.session_scope() as session:
        query = session.query(Restaurant).filter(Restaurant.platform.isnot(True))
        if slug:
            query = query.filter_by(slug=slug)
        return query.first().join_code
