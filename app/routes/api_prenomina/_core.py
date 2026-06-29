"""Núcleo compartido del paquete `api_prenomina`.

Define el blueprint `bp` y los helpers de uso común: parsing de fechas,
selección de reportes de la semana, serializers de prenómina y la generación
de PDF de recibos.

No registres rutas en este archivo. Las rutas viven en los submódulos por
dominio: semanas.py, ajustes.py, envio.py.
"""
import os
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from flask import Blueprint, current_app, render_template
from sqlalchemy.orm import joinedload

from app.models import (
    AjusteDescuento,
    Prenomina,
    Prestamo,
    RegistroDiarioHoras,
    ReporteSemanal,
    Trabajador,
)
from app.utils import to_dec

bp = Blueprint('api_prenomina', __name__, url_prefix='/api/prenomina')


# ── Helpers ────────────────────────────────────────────────────────────────────


def _parse_fecha(fecha_str: str):
    return datetime.strptime(fecha_str, '%Y-%m-%d').date()


def _reportes_de_semana(fecha_obj):
    return ReporteSemanal.query.filter(
        ReporteSemanal.fecha_inicio_semana == fecha_obj,
        ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA']),
    ).all()


def _trabajador_min(t) -> dict:
    if not t:
        return None
    return {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre': t.nombre,
        'nombre_apellidos': t.nombre_apellidos,
        'nombre_completo': t.nombre_completo,
        'correo': t.correo or '',
        'tipo_nomina': t.tipo_nomina or '',
        'tipo_pago': t.tipo_pago or '',
    }


def _num(v) -> float:
    if v is None:
        return 0.0
    return float(to_dec(v))


def _prenomina_dict(p: Prenomina, *, with_detail: bool = False) -> dict:
    data = {
        'id': p.id,
        'trabajador_id': p.trabajador_id,
        'trabajador': _trabajador_min(p.trabajador),
        'fecha_inicio': p.fecha_inicio.isoformat() if p.fecha_inicio else None,
        'fecha_fin': p.fecha_fin.isoformat() if p.fecha_fin else None,
        'estado': p.estado or 'PENDIENTE',
        'tipo_pago': p.tipo_pago or '',
        'total_horas_calculadas': _num(getattr(p, 'total_horas_calculadas', None)),
        # Percepciones
        'salario_base': _num(p.salario_base),
        'pago_horas_extras': _num(p.pago_horas_extras),
        'pago_viaticos': _num(p.pago_viaticos),
        'pago_festivos': _num(p.pago_festivos),
        'depositos_otros': _num(p.depositos_otros),
        'depositos_prestamos': _num(p.depositos_prestamos),
        # Deducciones
        'descuento_infonavit': _num(p.descuento_infonavit),
        'ajuste_inbursa': _num(p.ajuste_inbursa),
        'descuentos_otros': _num(p.descuentos_otros),
        'descuento_prestamos': _num(p.descuento_prestamos),
        'descuento_incidencias': _num(p.descuento_incidencias),
        'recuperacion_manual': _num(p.recuperacion_manual),
        # Totales
        'total_percepciones': _num(p.total_percepciones),
        'total_deducciones': _num(p.total_deducciones),
        'total_a_pagar': _num(p.total_a_pagar),
    }
    if with_detail and p.id is not None:
        data['descuentos_detalle'] = [
            {
                'id': d.id,
                'tipo': d.tipo,
                'concepto': d.concepto,
                'monto': _num(d.monto),
                'fecha_incidencia': d.fecha_incidencia.isoformat() if d.fecha_incidencia else None,
            } for d in (p.descuentos_detalle or [])
        ]
        data['depositos_detalle'] = [
            {
                'id': d.id,
                'concepto': d.concepto,
                'monto': _num(d.monto),
            } for d in (p.depositos_detalle or [])
        ]
    return data


def _build_recibos_data(reportes, prenominas):
    """Arma la estructura `recibos_data` que espera `recibo_pdf.html`."""
    reporte_ids = [r.id for r in reportes]
    todos_registros = RegistroDiarioHoras.query.options(
        joinedload(RegistroDiarioHoras.reporte).joinedload(ReporteSemanal.proyecto)
    ).filter(
        RegistroDiarioHoras.reporte_id.in_(reporte_ids)
    ).order_by(RegistroDiarioHoras.trabajador_id, RegistroDiarioHoras.fecha).all()

    por_trabajador = {}
    for reg in todos_registros:
        por_trabajador.setdefault(reg.trabajador_id, []).append(reg)

    reporte_generico = reportes[0]
    recibos = []
    for p in prenominas:
        regs = por_trabajador.get(p.trabajador_id, [])
        total_hrs = sum(r.horas_productivas or 0 for r in regs)
        proyectos_vistos = {}
        for reg in regs:
            if reg.reporte and reg.reporte.proyecto and not reg.incidencia:
                proy = reg.reporte.proyecto
                proyectos_vistos[proy.id] = proy
        nombre_proyecto = (
            ' | '.join(pr.nombre for pr in proyectos_vistos.values())
            if proyectos_vistos
            else (reporte_generico.proyecto.nombre if reporte_generico.proyecto else 'Sin asignar')
        )
        recibos.append({
            'p': p,
            'registros_trabajador': regs,
            'total_hrs': total_hrs,
            'nombre_proyecto': nombre_proyecto,
        })
    return recibos, reporte_generico


def _render_recibos_pdf(reportes, prenominas) -> BytesIO | None:
    """Genera el PDF con xhtml2pdf. Devuelve None si falla."""
    from xhtml2pdf import pisa

    recibos_data, reporte_generico = _build_recibos_data(reportes, prenominas)
    # API-only: la app se construye con `static_folder=None`. Resolvemos el
    # logo contra BASE_DIR; si no existe, el template recibe None y degrada
    # sin tronar.
    base_dir = current_app.config.get('BASE_DIR') or os.path.dirname(current_app.root_path)
    logo_path = os.path.join(base_dir, 'static', 'imagenes', 'skilled_white_bg.jpg')
    if not os.path.exists(logo_path):
        logo_path = None
    html_salida = render_template(
        'recibo_pdf.html',
        reporte=reporte_generico,
        recibos_data=recibos_data,
        logo_path=logo_path,
    )
    pdf = BytesIO()
    status = pisa.CreatePDF(BytesIO(html_salida.encode('utf-8')), dest=pdf)
    if status.err:
        return None
    pdf.seek(0)
    return pdf


def calcular_preview_prenomina(fecha_obj, reportes):
    """Toma una lista de ReporteSemanal de TODOS los proyectos de esa semana y
    devuelve una lista de Prenominas globales simuladas (en memoria, sin commit).

    Lógica de negocio originalmente en el legacy `prenomina.py`. Se movió aquí
    cuando ese archivo se borró — el contrato (firma y comportamiento) se
    mantiene idéntico para no romper a sus dos consumidores (`semanas.py` y
    `envio.py` dentro del mismo paquete).
    """
    preview = []

    # Batch-load de registros para todos los reportes: evita 1 query por reporte (N+1)
    reporte_ids = [r.id for r in reportes]
    registros_bulk = RegistroDiarioHoras.query.filter(
        RegistroDiarioHoras.reporte_id.in_(reporte_ids)
    ).all()
    registros_por_reporte = {}
    for reg in registros_bulk:
        registros_por_reporte.setdefault(reg.reporte_id, []).append(reg)

    # Obtenemos ids únicos de los trabajadores involucrados en esta semana
    trabajadores_ids = set()
    all_registros = []
    for r in reportes:
        for reg in registros_por_reporte.get(r.id, []):
            trabajadores_ids.add(reg.trabajador_id)
            all_registros.append(reg)

    if not trabajadores_ids:
        return preview

    fecha_fin_semana = reportes[0].fecha_fin_semana if reportes else None

    # Batch-load: un solo query para todos los trabajadores en vez de N queries individuales
    trabajadores_map = {t.id: t for t in Trabajador.query.filter(Trabajador.id.in_(trabajadores_ids)).all()}

    # Batch-load: un solo query para todos los préstamos activos de estos trabajadores
    todos_prestamos = Prestamo.query.filter(
        Prestamo.trabajador_id.in_(trabajadores_ids),
        Prestamo.estado == 'ACTIVO'
    ).all()
    prestamos_por_trabajador = {}
    for pr in todos_prestamos:
        prestamos_por_trabajador.setdefault(pr.trabajador_id, []).append(pr)

    # Batch-load: ajustes Inbursa de la semana para TODOS los trabajadores en un
    # solo query. Antes esto era un query por trabajador dentro del loop (N+1):
    # con 500 empleados eran 500 round-trips. La fecha de la semana es la misma
    # para todos, así que basta un IN + rango y luego agrupar en memoria.
    ajustes_bulk = AjusteDescuento.query.filter(
        AjusteDescuento.trabajador_id.in_(trabajadores_ids),
        AjusteDescuento.fecha_descuento >= fecha_obj,
        AjusteDescuento.fecha_descuento <= fecha_fin_semana,
    ).all()
    ajustes_por_trabajador = {}
    for aj in ajustes_bulk:
        ajustes_por_trabajador.setdefault(aj.trabajador_id, []).append(aj)

    # Agrupar registros por trabajador (sin queries adicionales)
    registros_por_trabajador = {}
    for reg in all_registros:
        registros_por_trabajador.setdefault(reg.trabajador_id, []).append(reg)

    for t_id in trabajadores_ids:
        trabajador = trabajadores_map.get(t_id)
        if not trabajador:
            continue

        registros_trabajador = registros_por_trabajador.get(t_id, [])

        # 1. Totalizar horas productivas consolidando proyectos
        total_horas = sum(r.horas_productivas or 0 for r in registros_trabajador)

        p = Prenomina(
            reporte_semanal_id=None,
            trabajador_id=trabajador.id,
            trabajador=trabajador,
            fecha_inicio=fecha_obj,
            fecha_fin=fecha_fin_semana,
            tipo_pago=trabajador.tipo_pago or 'EFECTIVO',
            pago_festivos=Decimal('0'),
            depositos_otros=Decimal('0'),
            depositos_prestamos=Decimal('0'),
            descuentos_otros=Decimal('0'),
            descuento_prestamos=Decimal('0'),
            descuento_incidencias=Decimal('0'),
            recuperacion_manual=Decimal('0')
        )

        # Guardar para visualización temporal en pantalla
        p.total_horas_calculadas = to_dec(total_horas)

        tipo = trabajador.tipo_nomina or 'Semanal'
        p.salario_base = Decimal('0')
        p.pago_horas_extras = Decimal('0')

        salario_pactado = to_dec(trabajador.salario_real_pactado_x_sem)

        if tipo == 'Por hora':
            p.salario_base = to_dec(total_horas) * salario_pactado
        elif tipo == 'Cuadrado':
            p.salario_base = salario_pactado
        else:  # Semanal
            p.salario_base = salario_pactado
            if total_horas > 50:
                horas_extras = to_dec(total_horas) - Decimal('50')
                costo_hr_extra = to_dec(trabajador.hr_extra)
                p.pago_horas_extras = horas_extras * costo_hr_extra

        # Deducciones maestras
        p.descuento_infonavit = to_dec(trabajador.infonavit)
        # Ajuste Inbursa: descuentos dinámicos del módulo de ajustes cuya fecha
        # cae dentro de la semana de prenómina (desde el batch pre-cargado, sin
        # query por trabajador). Si no hay, cae al monto fijo del perfil.
        ajuste_descuentos = ajustes_por_trabajador.get(t_id, [])
        if ajuste_descuentos:
            p.ajuste_inbursa = sum((to_dec(d.monto) for d in ajuste_descuentos), Decimal('0'))
        else:
            p.ajuste_inbursa = to_dec(trabajador.ajuste_inbursa)

        # Incidencias: NO se calcula descuento automático.
        # Las incidencias (Falta, Retardo, etc.) se muestran en la vista de edición
        # y el admin decide manualmente cuánto descontar vía el módulo de descuentos.
        # descuento_incidencias se mantiene en $0 hasta que el admin lo ajuste.

        # Viáticos: sumar por día usando monto manual o del perfil
        total_viaticos = Decimal('0')
        for reg in registros_trabajador:
            if reg.aplica_viaticos:
                if reg.monto_viaticos_manual is not None:
                    total_viaticos += to_dec(reg.monto_viaticos_manual)
                elif trabajador.viaticos:
                    total_viaticos += to_dec(trabajador.viaticos)
        p.pago_viaticos = total_viaticos

        # Pago por Días Festivos (toggle por registro)
        if trabajador.pago_dia_festivo:
            dias_festivos = sum(1 for reg in registros_trabajador if reg.aplica_dia_festivo)
            p.pago_festivos = to_dec(trabajador.pago_dia_festivo) * Decimal(str(dias_festivos))
        else:
            p.pago_festivos = Decimal('0')

        # Préstamos activos (desde el batch pre-cargado, sin query adicional)
        prestamos_activos = prestamos_por_trabajador.get(t_id, [])
        p.descuento_prestamos = sum((to_dec(pr.descuento_semanal) for pr in prestamos_activos), Decimal('0'))

        p.total_percepciones = to_dec(p.salario_base) + to_dec(p.pago_horas_extras) + to_dec(p.pago_viaticos) + to_dec(p.pago_festivos) + to_dec(p.depositos_otros)
        p.total_deducciones = to_dec(p.descuento_infonavit) + to_dec(p.ajuste_inbursa) + to_dec(p.descuento_incidencias) + to_dec(p.descuento_prestamos) + to_dec(p.descuentos_otros)
        p.total_a_pagar = p.total_percepciones - p.total_deducciones

        preview.append(p)

    return preview
