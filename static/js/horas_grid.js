(function () {
    var cfgEl    = document.getElementById('captura-config');
    var dataEl   = document.getElementById('registros-data');
    var semanaEl = document.getElementById('semana-config');
    if (!cfgEl || !dataEl || !semanaEl) return;

    var cfg      = JSON.parse(cfgEl.textContent);
    var regsData = JSON.parse(dataEl.textContent);   // array de registros
    var semana   = JSON.parse(semanaEl.textContent);  // {fechas, horas, incidencias}

    var BORRADOR   = cfg.borrador;
    var REPORTE_ID = cfg.reporte_id;
    var workerActivo = null;

    // ── Helpers ───────────────────────────────────────────────────────────────
    function getCSRF() {
        var el = document.querySelector('input[name="csrf_token"]');
        return el ? el.value : '';
    }

    function regDeTrabajadorFecha(trabajadorId, fecha) {
        return regsData.find(function (r) {
            return r.trabajador_id === trabajadorId && r.fecha === fecha;
        }) || null;
    }

    function selectHoras(cls) {
        var sel = document.createElement('select');
        sel.className = cls + ' form-control';
        sel.style.cssText = 'padding: 0.3rem 0.4rem; font-size: 0.82rem; min-width: 82px;';
        var opt0 = document.createElement('option');
        opt0.value = '';
        opt0.textContent = '--:--';
        sel.appendChild(opt0);
        semana.horas.forEach(function (h) {
            var o = document.createElement('option');
            o.value = h;
            o.textContent = h;
            sel.appendChild(o);
        });
        return sel;
    }

    function selectIncidencias(cls) {
        var sel = document.createElement('select');
        sel.className = cls + ' form-control';
        sel.style.cssText = 'padding: 0.3rem 0.4rem; font-size: 0.82rem; width: 100%;';
        var opt0 = document.createElement('option');
        opt0.value = '';
        opt0.textContent = 'Sin Incidencia';
        sel.appendChild(opt0);
        semana.incidencias.forEach(function (inc) {
            var o = document.createElement('option');
            o.value = inc;
            o.textContent = inc;
            sel.appendChild(o);
        });
        return sel;
    }

    function chk(accentColor) {
        var c = document.createElement('input');
        c.type = 'checkbox';
        c.style.cssText = 'width:16px; height:16px; cursor:pointer;' + (accentColor ? ' accent-color:' + accentColor + ';' : '');
        return c;
    }

    function actualizarTotalModal(trabajadorId) {
        var total = regsData
            .filter(function (r) { return r.trabajador_id === trabajadorId; })
            .reduce(function (s, r) { return s + (r.horas_productivas || 0); }, 0);
        var el = document.getElementById('mtsTotalHrs');
        if (el) el.textContent = 'Total semana: ' + total.toFixed(1) + ' hrs';
    }

    function actualizarContadorCard(trabajadorId) {
        var card = document.querySelector('.worker-card[data-trabajador-id="' + trabajadorId + '"]');
        if (!card) return;
        var dias = regsData.filter(function (r) { return r.trabajador_id === trabajadorId; }).length;
        var total = regsData
            .filter(function (r) { return r.trabajador_id === trabajadorId; })
            .reduce(function (s, r) { return s + (r.horas_productivas || 0); }, 0);
        var span = card.querySelector('.card-dias');
        if (span) {
            span.style.color = dias > 0 ? 'var(--success)' : 'var(--text-muted)';
            span.textContent = dias + '/' + semana.fechas.length + ' días';
        }
        var spanTotal = card.querySelector('.card-total');
        if (spanTotal) {
            spanTotal.textContent = total > 0 ? '· ' + total.toFixed(1) + 'h' : '';
        }
    }

    // ── Construir fila de día ─────────────────────────────────────────────────
    function crearFilaDia(fechaObj, trabajadorId) {
        var fecha   = fechaObj.fecha;
        var reg     = regDeTrabajadorFecha(trabajadorId, fecha);
        var esFinSemana = fechaObj.fin_semana;

        var tr = document.createElement('tr');
        tr.dataset.fecha = fecha;
        tr.dataset.registroId = reg ? String(reg.id) : '';
        tr.style.cssText = 'border-bottom: 1px solid #F0F0F0;' + (esFinSemana ? ' background: #FAFAFA;' : '');

        // Día
        var tdDia = document.createElement('td');
        tdDia.style.cssText = 'padding: 0.55rem 0.75rem; white-space: nowrap;';
        tdDia.innerHTML = '<strong style="' + (esFinSemana ? 'color:#7C3AED;' : '') + '">' + fechaObj.dia + '</strong>'
            + '<br><span style="font-size:0.73rem; color:var(--text-muted);">' + fechaObj.label + '</span>';
        tr.appendChild(tdDia);

        // Entrada
        var tdE = document.createElement('td');
        tdE.style.padding = '0.4rem 0.3rem';
        var selE = selectHoras('sel-entrada');
        selE.value = reg ? reg.hora_entrada : '';
        if (!BORRADOR) selE.disabled = true;
        tdE.appendChild(selE);
        tr.appendChild(tdE);

        // Salida
        var tdS = document.createElement('td');
        tdS.style.padding = '0.4rem 0.3rem';
        var selS = selectHoras('sel-salida');
        selS.value = reg ? reg.hora_salida : '';
        if (!BORRADOR) selS.disabled = true;
        tdS.appendChild(selS);
        tr.appendChild(tdS);

        // Comida
        var tdC = document.createElement('td');
        tdC.style.cssText = 'text-align:center; padding: 0.4rem 0.25rem;';
        var chkC = chk();
        chkC.checked = reg ? reg.tomo_comida : false;
        if (!BORRADOR) chkC.disabled = true;
        tdC.appendChild(chkC);
        tr.appendChild(tdC);

        // Viáticos
        var tdV = document.createElement('td');
        tdV.style.cssText = 'text-align:center; padding: 0.4rem 0.25rem;';
        var chkV = chk('#D97706');
        chkV.checked = reg ? reg.aplica_viaticos : false;
        if (!BORRADOR) chkV.disabled = true;
        tdV.appendChild(chkV);
        tr.appendChild(tdV);

        // Festivo
        var tdF = document.createElement('td');
        tdF.style.cssText = 'text-align:center; padding: 0.4rem 0.25rem;';
        var chkF = chk('#7C3AED');
        chkF.checked = reg ? reg.aplica_dia_festivo : false;
        if (!BORRADOR) chkF.disabled = true;
        tdF.appendChild(chkF);
        tr.appendChild(tdF);

        // Incidencia
        var tdI = document.createElement('td');
        tdI.style.padding = '0.4rem 0.3rem';
        var selI = selectIncidencias('sel-incidencia');
        selI.value = reg ? reg.incidencia : '';
        if (!BORRADOR) selI.disabled = true;
        tdI.appendChild(selI);
        tr.appendChild(tdI);

        // Hrs resultado
        var tdHrs = document.createElement('td');
        tdHrs.style.cssText = 'text-align:center; padding:0.4rem 0.3rem; font-weight:700; color:var(--success); white-space:nowrap;';
        tdHrs.textContent = reg && reg.horas_productivas > 0 ? reg.horas_productivas.toFixed(1) + 'h' : '\u2014';
        tr.appendChild(tdHrs);

        // Acciones
        var tdAcc = document.createElement('td');
        tdAcc.style.cssText = 'padding:0.4rem 0.5rem; vertical-align:middle;';

        if (BORRADOR) {
            var statusDiv = document.createElement('div');
            statusDiv.style.cssText = 'font-size:0.72rem; min-height:14px; margin-bottom:3px; text-align:center;';

            var btnG = document.createElement('button');
            btnG.type = 'button';
            btnG.textContent = reg ? 'Actualizar' : 'Guardar';
            btnG.style.cssText = 'padding:0.28rem 0.65rem; font-size:0.78rem; border-radius:6px; border:none; cursor:pointer; background:var(--primary); color:white; font-weight:600; width:100%; transition:opacity .15s;';

            btnG.addEventListener('click', function () {
                guardarDia(tr, trabajadorId, {
                    selE: selE, selS: selS, chkC: chkC,
                    chkV: chkV, chkF: chkF, selI: selI,
                    tdHrs: tdHrs, statusDiv: statusDiv, btnG: btnG
                });
            });

            tdAcc.appendChild(statusDiv);
            tdAcc.appendChild(btnG);

            if (reg) {
                var btnDel = document.createElement('button');
                btnDel.type = 'button';
                btnDel.textContent = 'Eliminar';
                btnDel.style.cssText = 'margin-top:3px; padding:0.18rem 0.5rem; font-size:0.72rem; border-radius:5px; border:1px solid #FCA5A5; cursor:pointer; background:#FEF2F2; color:#B91C1C; width:100%;';
                btnDel.addEventListener('click', function () {
                    eliminarDia(tr, reg.id, trabajadorId, statusDiv, tdHrs, btnDel);
                });
                tdAcc.appendChild(btnDel);
            }
        } else if (reg) {
            tdAcc.innerHTML = '<span style="font-size:0.78rem; color:var(--text-muted);">Cerrado</span>';
        }

        tr.appendChild(tdAcc);
        return tr;
    }

    // ── Guardar día vía AJAX ──────────────────────────────────────────────────
    async function guardarDia(tr, trabajadorId, f) {
        var registroId = tr.dataset.registroId;
        var fecha = tr.dataset.fecha;
        var entrada = f.selE.value;
        var salida  = f.selS.value;
        var incidencia = f.selI.value;

        if (!registroId && !entrada && !salida && !incidencia) {
            f.statusDiv.style.color = '#9CA3AF';
            f.statusDiv.textContent = 'Sin datos';
            return;
        }

        var url = registroId
            ? '/horas/editar_registro/' + registroId
            : '/horas/guardar_registro/' + REPORTE_ID;

        var fd = new FormData();
        fd.append('csrf_token', getCSRF());
        fd.append('hora_entrada', entrada);
        fd.append('hora_salida', salida);
        if (f.chkC.checked) fd.append('tomo_comida', '1');
        if (f.chkV.checked) {
            fd.append('aplica_viaticos', '1');
            fd.append('viaticos_modo', 'perfil');
        }
        if (f.chkF.checked) fd.append('aplica_dia_festivo', '1');
        fd.append('incidencia', incidencia);
        if (!registroId) {
            fd.append('trabajador_id', trabajadorId);
            fd.append('fecha', fecha);
        }

        f.btnG.disabled = true;
        f.btnG.style.opacity = '0.6';
        f.statusDiv.textContent = '';

        try {
            var resp = await fetch(url, {
                method: 'POST',
                body: fd,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            var data = await resp.json();

            if (data.ok) {
                f.statusDiv.style.color = '#059669';
                f.statusDiv.textContent = '\u2713 Guardado';
                f.btnG.textContent = 'Actualizar';
                f.tdHrs.textContent = data.registro.horas_productivas > 0
                    ? data.registro.horas_productivas.toFixed(1) + 'h' : '\u2014';

                // Actualizar caché local
                var idx = regsData.findIndex(function (r) { return r.id === data.registro.id; });
                if (idx >= 0) {
                    regsData[idx] = Object.assign(regsData[idx], data.registro);
                } else {
                    regsData.push(Object.assign({ trabajador_id: trabajadorId, fecha: fecha }, data.registro));
                }
                tr.dataset.registroId = String(data.registro.id);

                // Agregar botón Eliminar si es registro nuevo
                if (!registroId && !f.btnG.parentElement.querySelector('.btn-eliminar')) {
                    var btnDel = document.createElement('button');
                    btnDel.type = 'button';
                    btnDel.className = 'btn-eliminar';
                    btnDel.textContent = 'Eliminar';
                    btnDel.style.cssText = 'margin-top:3px; padding:0.18rem 0.5rem; font-size:0.72rem; border-radius:5px; border:1px solid #FCA5A5; cursor:pointer; background:#FEF2F2; color:#B91C1C; width:100%;';
                    (function (id) {
                        btnDel.addEventListener('click', function () {
                            eliminarDia(tr, id, trabajadorId, f.statusDiv, f.tdHrs, btnDel);
                        });
                    })(data.registro.id);
                    f.btnG.parentElement.appendChild(btnDel);
                }

                sincronizarFilaTabla(data.registro, !registroId);
                actualizarTotalModal(trabajadorId);
                actualizarContadorCard(trabajadorId);
            } else {
                f.statusDiv.style.color = '#DC2626';
                f.statusDiv.textContent = data.error || 'Error';
            }
        } catch (e) {
            f.statusDiv.style.color = '#DC2626';
            f.statusDiv.textContent = 'Error de red';
        }

        f.btnG.disabled = false;
        f.btnG.style.opacity = '1';
    }

    // ── Eliminar día vía AJAX ─────────────────────────────────────────────────
    async function eliminarDia(tr, registroId, trabajadorId, statusDiv, tdHrs, btnDel) {
        if (!confirm('\u00bfEliminar este registro?')) return;

        var fd = new FormData();
        fd.append('csrf_token', getCSRF());
        btnDel.disabled = true;

        try {
            var resp = await fetch('/horas/eliminar_registro/' + registroId, {
                method: 'POST',
                body: fd,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            var data = await resp.json();

            if (data.ok) {
                regsData = regsData.filter(function (r) { return r.id !== registroId; });
                tr.dataset.registroId = '';
                tdHrs.textContent = '\u2014';
                statusDiv.style.color = '#9CA3AF';
                statusDiv.textContent = 'Eliminado';
                btnDel.remove();
                eliminarFilaTabla(registroId);
                actualizarTotalModal(trabajadorId);
                actualizarContadorCard(trabajadorId);
            } else {
                statusDiv.style.color = '#DC2626';
                statusDiv.textContent = data.error || 'Error';
                btnDel.disabled = false;
            }
        } catch (e) {
            statusDiv.style.color = '#DC2626';
            statusDiv.textContent = 'Error de red';
            btnDel.disabled = false;
        }
    }

    // ── Sincronización tabla "Registros Guardados" ────────────────────────────
    var DIAS_ABREV = ['Dom','Lun','Mar','Mié','Jue','Vie','Sáb'];

    function fechaDiaCorto(fechaStr) {
        var parts = fechaStr.split('-');
        var d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
        var dia = DIAS_ABREV[d.getDay()];
        var dd = String(d.getDate()).padStart(2, '0');
        var mm = String(d.getMonth() + 1).padStart(2, '0');
        return dia + ' ' + dd + '/' + mm;
    }

    function actualizarContadorTabla(delta) {
        var headers = document.querySelectorAll('.captura-form-bg h3');
        headers.forEach(function (h) {
            if (h.textContent.indexOf('Registros Guardados') >= 0) {
                var m = h.textContent.match(/\((\d+)\)/);
                if (m) h.textContent = 'Registros Guardados (' + (parseInt(m[1]) + delta) + ')';
            }
        });
    }

    function crearFilaTabla(reg) {
        var diaCorto = fechaDiaCorto(reg.fecha);
        var diaAbrev = diaCorto.split(' ')[0];

        var tr = document.createElement('tr');
        tr.dataset.registroId = String(reg.id);
        tr.dataset.dia = diaAbrev;
        tr.dataset.trabajador = ((reg.nombre_completo || '') + ' ' + (reg.no_empleado || '')).toLowerCase();

        function td(html, style) {
            var el = document.createElement('td');
            if (style) el.style.cssText = style;
            el.innerHTML = html;
            return el;
        }

        tr.appendChild(td('<strong>' + diaCorto + '</strong>'));
        tr.appendChild(td('<span style="font-family:monospace;">' + (reg.no_empleado || '') + '</span>'));
        tr.appendChild(td(reg.nombre_completo || ''));
        tr.appendChild(td(reg.hora_entrada || '-'));
        tr.appendChild(td(reg.hora_salida || '-'));
        tr.appendChild(td(reg.tomo_comida ? '<span style="color:var(--primary);font-weight:bold;">Sí</span>' : '<span style="color:var(--text-muted);">No</span>'));
        tr.appendChild(td(reg.aplica_viaticos ? '<span style="color:#D97706;font-weight:bold;">Sí</span>' : '<span style="color:var(--text-muted);">No</span>'));
        tr.appendChild(td(reg.aplica_dia_festivo ? '<span style="color:#7C3AED;font-weight:bold;">Sí</span>' : '<span style="color:var(--text-muted);">No</span>'));
        tr.appendChild(td((reg.horas_productivas || 0).toFixed(2) + ' hrs.', 'font-weight:bold; color:var(--success);'));
        tr.appendChild(td(reg.incidencia ? '<span class="badge" style="background:#FEE2E2;color:#B91C1C;">' + reg.incidencia + '</span>' : '-'));

        var tdAcc = document.createElement('td');
        var btnDel = document.createElement('button');
        btnDel.type = 'button';
        btnDel.className = 'delete-btn';
        btnDel.title = 'Eliminar registro';
        btnDel.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
        (function (regId, trabajadorId) {
            btnDel.addEventListener('click', function () {
                if (!confirm('¿Eliminar este registro?')) return;
                var fd = new FormData();
                fd.append('csrf_token', getCSRF());
                fetch('/horas/eliminar_registro/' + regId, {
                    method: 'POST', body: fd,
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                }).then(function (r) { return r.json(); }).then(function (data) {
                    if (data.ok) {
                        tr.remove();
                        actualizarContadorTabla(-1);
                        regsData = regsData.filter(function (r) { return r.id !== regId; });
                        actualizarTotalModal(trabajadorId);
                        actualizarContadorCard(trabajadorId);
                    }
                });
            });
        })(reg.id, reg.trabajador_id);
        tdAcc.appendChild(btnDel);
        tr.appendChild(tdAcc);
        return tr;
    }

    function sincronizarFilaTabla(reg, esNuevo) {
        var tbody = document.querySelector('.history-table tbody');
        if (!tbody) return;
        var existente = tbody.querySelector('tr[data-registro-id="' + reg.id + '"]');
        if (existente) {
            var nueva = crearFilaTabla(reg);
            tbody.replaceChild(nueva, existente);
        } else if (esNuevo) {
            // Quitar fila de "sin registros" si existe
            var sinRegs = tbody.querySelector('tr:not([data-dia]):not(#sinResultadosFiltro)');
            if (sinRegs) sinRegs.remove();
            tbody.insertBefore(crearFilaTabla(reg), tbody.firstChild);
            actualizarContadorTabla(1);
        }
    }

    function eliminarFilaTabla(registroId) {
        var tbody = document.querySelector('.history-table tbody');
        if (!tbody) return;
        var tr = tbody.querySelector('tr[data-registro-id="' + registroId + '"]');
        if (tr) {
            tr.remove();
            actualizarContadorTabla(-1);
        }
    }

    // ── Abrir modal de trabajador ─────────────────────────────────────────────
    function abrirModal(card) {
        var trabajadorId = parseInt(card.dataset.trabajadorId, 10);
        var nombre = card.dataset.nombre;
        var no     = card.dataset.no;

        // Marcar tarjeta activa
        document.querySelectorAll('.worker-card').forEach(function (c) { c.classList.remove('activo'); });
        card.classList.add('activo');
        workerActivo = trabajadorId;

        document.getElementById('mtsNombre').textContent = nombre;
        document.getElementById('mtsNo').textContent = no;

        var tbody = document.getElementById('cuerpoSemanaTrabajador');
        tbody.innerHTML = '';
        semana.fechas.forEach(function (fechaObj) {
            tbody.appendChild(crearFilaDia(fechaObj, trabajadorId));
        });

        actualizarTotalModal(trabajadorId);
        document.getElementById('modalTrabajador').classList.add('active');
    }

    function cerrarModal() {
        document.getElementById('modalTrabajador').classList.remove('active');
        document.querySelectorAll('.worker-card').forEach(function (c) { c.classList.remove('activo'); });
        workerActivo = null;
    }

    // ── Event listeners ───────────────────────────────────────────────────────
    document.getElementById('listaWorkerCards').addEventListener('click', function (e) {
        var card = e.target.closest('.worker-card');
        if (card) abrirModal(card);
    });

    document.getElementById('btnCerrarMts').addEventListener('click', cerrarModal);
    document.getElementById('btnCerrarMts2').addEventListener('click', cerrarModal);
    document.getElementById('modalTrabajador').addEventListener('click', function (e) {
        if (e.target === this) cerrarModal();
    });

    // ── Buscador de tarjetas de trabajador ────────────────────────────────────
    var inputBuscar = document.getElementById('buscarWorkerCard');
    if (inputBuscar) {
        inputBuscar.addEventListener('input', function () {
            var q = this.value.trim().toLowerCase();
            var cards = document.querySelectorAll('#listaWorkerCards .worker-card');
            var visible = 0;
            cards.forEach(function (c) {
                var texto = ((c.dataset.nombre || '') + ' ' + (c.dataset.no || '')).toLowerCase();
                var match = !q || texto.includes(q);
                c.style.display = match ? '' : 'none';
                if (match) visible++;
            });
            var noRes = document.getElementById('workerCardNoResults');
            if (noRes) noRes.style.display = (visible === 0 && q) ? '' : 'none';
        });
    }
})();
