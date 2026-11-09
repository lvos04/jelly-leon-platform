"""jelly-leon-platform — lifecycle-dienst van het TrueNAS Home Platform V2.

Eén API-dienst (geen eigen UI behalve een kleine statuspagina) die installatie, updates, rollback en
verwijderen/verhuizen van het hele platform plant en vastlegt. Uitvoering via de executor volgt in fase P2;
tot die tijd geven niet-gebouwde functies eerlijk ``capability_unavailable``.
"""

__version__ = "0.1.0"
APP_ID = "jelly-leon-platform"
SCHEMA_VERSION = "1.0"
