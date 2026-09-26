"""[01306] En qué moneda trabaja la casa.

Un número suelto en una pantalla —«Valor en cámara: 1573»— no dice si son
euros, dólares o kilos. La casa elige su moneda una vez y a partir de ahí cada
cifra de dinero lleva su símbolo al lado, en el nombre de la columna o del
recuadro, que es como ya se dicen los kilos.

Aquí no se convierte nada. Una casa cobra y paga en una moneda y esa es la
única que ve; convertir de una a otra exige un cambio del día, y un cambio
viejo miente más que no poner nada.
"""

# [01309] El símbolo que se enseña. Donde varias monedas comparten el «$» se escribe
# con su letra delante: en una casa que trabaja en pesos, «$» a secas se lee
# como dólares y no lo es.
MONEDAS: dict[str, str] = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "CHF": "CHF",
    "MXN": "MX$",
    "ARS": "AR$",
    "CLP": "CL$",
    "COP": "CO$",
    "BRL": "R$",
    "AUD": "A$",
    "CAD": "C$",
    # El Golfo. Estaba el dirham y faltaban las demás, así que una casa de
    # Riad no podía ni elegir su moneda en la configuración. El riyal
    # saudí, el catarí y el dinar de Kuwait, Baréin y Omán se escriben con
    # su código: el símbolo en árabe no lo pinta igual todo el mundo, y un
    # código de tres letras no se confunde nunca.
    # Ojo con los tres últimos: KWD, BHD y OMR llevan **tres** decimales,
    # y este programa cuenta en céntimos (dos). Hasta que eso se mire con
    # calma, en esas tres monedas los costes salen redondeados al fils.
    "AED": "AED",
    "SAR": "SAR",
    "QAR": "QAR",
    "KWD": "KWD",
    "BHD": "BHD",
    "OMR": "OMR",
    "MAD": "MAD",
    "HUF": "Ft",
    "PLN": "zł",
    "SEK": "kr",
    "NOK": "kr",
    "DKK": "kr",
    "JPY": "¥",
}

POR_DEFECTO = "EUR"


def es_valida(code: str) -> bool:
    """[01307] Si esa moneda es una de las que conoce el programa."""
    return (code or "").upper() in MONEDAS


def simbolo(code: str | None) -> str:
    """[01308] El símbolo de esa moneda. Si no se reconoce, se enseña su código."""
    if not code:
        return MONEDAS[POR_DEFECTO]
    return MONEDAS.get(code.upper(), code.upper())
