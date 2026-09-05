"""
Shafsky Aviation — Production Airport Package Seeders.

Authoritative package configurations modularized per airport.
"""

from app.seeds.airports.amd import *
from app.seeds.airports.hyd import *
from app.seeds.airports.del_t3 import *
from app.seeds.airports.lko import *
from app.seeds.airports.ccu import *
from app.seeds.airports.bom import *
from app.seeds.airports.goi import *
from app.seeds.airports.jai import *
from app.seeds.airports.atq import *
from app.seeds.airports.gau import *
from app.seeds.airports.bbi import *
from app.seeds.airports.vtz import *
from app.seeds.airports.maa import *
from app.seeds.airports.ixe import *
from app.seeds.airports.cok import *
from app.seeds.airports.blr import *
from app.seeds.airports.ixc import *
from app.seeds.airports.ixr import *
from app.seeds.airports.gox import *

AIRPORT_SEEDERS = {
    "AMD": seed_amd_production_packages,
    "BOM": seed_bom_production_packages,
    "GOI": seed_goi_production_packages,
    "JAI": seed_jai_production_packages,
    "ATQ": seed_atq_production_packages,
    "GAU": seed_gau_production_packages,
    "HYD": seed_hyd_production_packages,
    "DEL": seed_del_production_packages,
    "LKO": seed_lko_production_packages,
    "CCU": seed_ccu_production_packages,
    "BBI": seed_bbi_production_packages,
    "VTZ": seed_vtz_production_packages,
    "MAA": seed_maa_production_packages,
    "IXE": seed_ixe_production_packages,
    "COK": seed_cok_production_packages,
    "BLR": seed_blr_production_packages,
    "IXC": seed_ixc_production_packages,
    "IXR": seed_ixr_production_packages,
    "GOX": seed_gox_production_packages,
}
