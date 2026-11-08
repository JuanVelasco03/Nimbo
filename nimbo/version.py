"""Version de Nimbo, fuente unica (SPEC 17).

Antes vivia escrita a mano en empaquetado\\nimbo.iss (`#define Version
"1.0.0"`) y en ningun otro lado: ni paquete.json ni el chequeo de
actualizacion (nimbo\\actualizaciones.py) tenian de donde leerla. Este
modulo es ahora lo unico que hay que tocar para subir la version.
"""

VERSION = "1.0.0"
