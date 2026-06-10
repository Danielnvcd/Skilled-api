"""Paquete `app.models` — partición por dominio + re-exports.

El módulo original `app/models.py` (977 líneas, 42 clases) está dividido por
dominio funcional. Todos los símbolos públicos se re-exportan desde aquí,
por lo que `from app.models import X` sigue funcionando sin cambios en los
consumidores.

  _base.py         _now_utc (helper compartido)
  auth.py          User, RefreshToken, TwoFactorBackupCode, AuditLog
  trabajador.py    Trabajador, CredencialPlanta, DocumentoTrabajador
  proyecto.py      Proyecto, proyecto_trabajador (tabla M2M)
  horas.py         ReporteSemanal, RegistroDiarioHoras, Ausencia, SaldoVacaciones
  prenomina.py     Prenomina, DescuentoPrenomina, DepositoExtra
  prestamos.py     Prestamo, AbonoPrestamo
  ajustes.py       AjustePeriodo, AjusteTrabajadorPeriodo, AjusteDescuento
  inventario.py    Almacen, Estante, Producto, NotificacionUmbral,
                   StockPorAlmacen, TomaInventario(+Detalle, ESTADOS_TOMA),
                   ProductoEstante, MovimientoInventario, CategoriaConfig
  solicitudes.py   SolicitudMaterial, SolicitudMaterialDetalle
  notificaciones.py Notificacion + crear_notif_admins / crear_notif_inventario
  herramientas.py  HerramientaCategoria, Herramienta, HerramientaUnidad,
                   AsignacionHerramienta, MantenimientoHerramienta,
                   IncidenciaHerramienta, SolicitudBajaHerramienta,
                   EventoHerramienta, MediaHerramienta,
                   crear_evento_herramienta + catálogos ESTADOS_*/TIPO_*

Orden de import: como SQLAlchemy resuelve `db.relationship('Nombre')` y
`db.ForeignKey('tabla.col')` por string al momento de configuración del
mapper (después de que TODAS las clases se importen), el orden aquí abajo
no afecta la resolución de relaciones — pero seguimos el orden de dependencia
de FK como guía mental para quien lea.
"""
from app.models._base import _now_utc

from app.models.auth import (
    AuditLog,
    RefreshToken,
    TwoFactorBackupCode,
    User,
)

from app.models.trabajador import (
    CredencialPlanta,
    DocumentoTrabajador,
    NotaTrabajador,
    Trabajador,
)

from app.models.proyecto import (
    Proyecto,
    proyecto_trabajador,
)

from app.models.horas import (
    Ausencia,
    RegistroDiarioHoras,
    ReporteSemanal,
    SaldoVacaciones,
)

from app.models.prenomina import (
    DepositoExtra,
    DescuentoPrenomina,
    Prenomina,
)

from app.models.prestamos import (
    AbonoPrestamo,
    Prestamo,
)

from app.models.ajustes import (
    AjusteDescuento,
    AjustePeriodo,
    AjusteTrabajadorPeriodo,
)

from app.models.inventario import (
    ESTADOS_TOMA,
    Almacen,
    CategoriaConfig,
    Estante,
    MovimientoInventario,
    NotificacionUmbral,
    Producto,
    ProductoEstante,
    StockPorAlmacen,
    TomaInventario,
    TomaInventarioDetalle,
)

from app.models.solicitudes import (
    SolicitudMaterial,
    SolicitudMaterialDetalle,
)

# notificaciones depende de auth.User → debe ir DESPUÉS de auth.
from app.models.notificaciones import (
    Notificacion,
    crear_notif_admins,
    crear_notif_inventario,
)

# herramientas depende de varios módulos previos (User, Trabajador, Almacen,
# Estante, SolicitudMaterial) — todos ya importados arriba.
from app.models.herramientas import (
    CONDICION_HERRAMIENTA,
    ESTADOS_ASIGNACION,
    ESTADOS_INCIDENCIA,
    ESTADOS_MANTENIMIENTO,
    ESTADOS_SOLICITUD_BAJA,
    ESTADOS_UNIDAD,
    AsignacionHerramienta,
    EventoHerramienta,
    Herramienta,
    HerramientaCategoria,
    HerramientaUnidad,
    IncidenciaHerramienta,
    MantenimientoHerramienta,
    MediaHerramienta,
    SolicitudBajaHerramienta,
    TIPO_EVENTO_HERRAMIENTA,
    TIPO_INCIDENCIA,
    TIPO_ITEM_SOLICITUD,
    TIPO_MANTENIMIENTO,
    USO_HERRAMIENTA,
    crear_evento_herramienta,
)


__all__ = [
    # _base
    '_now_utc',
    # auth
    'User', 'RefreshToken', 'TwoFactorBackupCode', 'AuditLog',
    # trabajador
    'Trabajador', 'CredencialPlanta', 'DocumentoTrabajador', 'NotaTrabajador',
    # proyecto
    'Proyecto', 'proyecto_trabajador',
    # horas
    'ReporteSemanal', 'RegistroDiarioHoras', 'Ausencia', 'SaldoVacaciones',
    # prenomina
    'Prenomina', 'DescuentoPrenomina', 'DepositoExtra',
    # prestamos
    'Prestamo', 'AbonoPrestamo',
    # ajustes
    'AjustePeriodo', 'AjusteTrabajadorPeriodo', 'AjusteDescuento',
    # inventario
    'Almacen', 'Estante', 'Producto', 'NotificacionUmbral',
    'StockPorAlmacen', 'TomaInventario', 'TomaInventarioDetalle',
    'ProductoEstante', 'MovimientoInventario', 'CategoriaConfig',
    'ESTADOS_TOMA',
    # solicitudes
    'SolicitudMaterial', 'SolicitudMaterialDetalle',
    # notificaciones
    'Notificacion', 'crear_notif_admins', 'crear_notif_inventario',
    # herramientas
    'HerramientaCategoria', 'Herramienta', 'HerramientaUnidad',
    'AsignacionHerramienta', 'MantenimientoHerramienta',
    'IncidenciaHerramienta', 'SolicitudBajaHerramienta',
    'EventoHerramienta', 'MediaHerramienta',
    'crear_evento_herramienta',
    'ESTADOS_UNIDAD', 'ESTADOS_ASIGNACION', 'ESTADOS_MANTENIMIENTO',
    'ESTADOS_INCIDENCIA', 'ESTADOS_SOLICITUD_BAJA', 'TIPO_ITEM_SOLICITUD',
    'USO_HERRAMIENTA', 'TIPO_INCIDENCIA', 'TIPO_MANTENIMIENTO',
    'CONDICION_HERRAMIENTA', 'TIPO_EVENTO_HERRAMIENTA',
]
