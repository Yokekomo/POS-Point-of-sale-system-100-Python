"""Importadores (Fase 1, pendientes): POS PDF (pdfplumber), facturas (OCR),
hojas manuscritas (visión).

Cada importador devuelve (registros, SourceStatus) y NUNCA colapsa
'no se pudo leer' en 'cero'. Ver thegrill.rules.classify_source.
"""
