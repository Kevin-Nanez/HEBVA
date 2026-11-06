"""Fixtures de la evaluación (T5.6): las copias A a D, los tickets adversariales de K31 y su reporte.

- copias.py arma las copias alteradas de datos/ y devuelve su carpeta y su base, que es lo que recibe responder().
- tickets_adversariales.py genera los tickets de los 5 objetivos de K31 y califica la respuesta a cada uno.
- reporte_adversarial.py corre los casos y da los conteos, la tasa de falsos positivos y el sha256 de antes y de después.
- casos_huecos.py y reporte_huecos.py hacen lo mismo con los ataques que K31 no cubre (cifra lavada, título hostil y política envenenada).

datos/ no se toca: cada copia se deriva de sus bytes en eval/fixtures/generadas/, que git no versiona.
"""
